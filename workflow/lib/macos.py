"""macOS pull backend for the shared workflow state machine.

Guest commands, inputs and artifacts travel via the durable worker queue.
No control-plane SSH/SCP connection to a Mac is used.
"""
import base64
import fcntl
import hashlib
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time
import tempfile
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workers" / "lib"))
from client import Client


def backend(Run):
    class MacRun(Run):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            unsupported = {s["code"] for s in self.wf["steps"] if "code" in s} - {"gates.sh", "pr-create.sh"}
            if unsupported: raise ValueError("unsupported Mac code steps: " + ", ".join(sorted(unsupported)))
            self.work = str(pathlib.PurePosixPath(self.project["app_dir"]).parent / "work" / self.task)
            self.env_file = str(pathlib.PurePosixPath(self.work) / "runtime.env")
            self.client = None
            self.run_lock = None
            self.state["backend"] = "macos-pull"
            self.state["worker"] = self.project["worker"]
            if self.resume:
                for field in ("finished", "error", "result"):
                    self.state.pop(field, None)

        def main(self):
            try:
                return super().main()
            except Exception as e:
                import datetime
                self.state.update(result="human", error=str(e),
                                  finished=datetime.datetime.now().isoformat(timespec="seconds"))
                self.save()
                self.log(f"Mac execution failed: {e}; existing lease retained for inspection")
                return 2

        def run_agent(self, step, retry_note=""):
            self.refresh_token()
            return super().run_agent(step, retry_note)

        def command(self, cmd):
            return ("export PATH=/opt/homebrew/opt/coreutils/libexec/gnubin:/opt/homebrew/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH; "
                    f"export SANDBOX_APP_DIR={shlex.quote(self.project['app_dir'])}; "
                    f"if test -f {shlex.quote(self.env_file)}; then source {shlex.quote(self.env_file)}; fi; " + cmd)

        def sb(self, cmd, input_text=None, check=True):
            if self.dry: return ""
            _, r = self.client.execute("guest-exec", {"command": self.command(cmd), "timeout": 600}, stdin=input_text)
            if check and r.returncode:
                raise RuntimeError(f"guest command failed ({r.returncode}): {r.stdout[-1500:]}")
            return r.stdout

        def scp_to(self, local, remote):
            if self.dry: return
            data = pathlib.Path(local).read_bytes()
            # Small workflow inputs only, never host paths or arbitrary archives.
            if len(data) > 350000: raise RuntimeError("workflow input exceeds transfer limit")
            script = "import sys,base64,pathlib; p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(base64.b64decode(sys.stdin.read(),validate=True)); p.chmod(0o600)"
            self.sb(f"python3 -c {shlex.quote(script)} {shlex.quote(remote)}", input_text=base64.b64encode(data).decode())

        def run_remote(self, cmd, log_path, render=None, timeout=3600):
            pending, out = "", []
            with open(log_path, "a", encoding="utf-8") as f:
                def emit(chunk):
                    nonlocal pending
                    pending += chunk
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        value = render(line) if render else line
                        if value is not None:
                            value = value.rstrip("\n") + "\n"
                            f.write(value); f.flush(); out.append(value)
                _, r = self.client.execute("guest-exec", {"command": self.command(cmd), "timeout": min(timeout, 3600)}, emit=emit)
                if pending: emit("\n")
            return r.returncode, "".join(out)

        def take(self):
            if self.dry: return
            import aifactory_paths as paths
            self.run_lock = open(self.run_dir / "macos.lock", "a")
            fcntl.flock(self.run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lease = self.state.get("lease") if self.resume else f"run-{self.task}-{uuid.uuid4().hex[:16]}"
            if not lease: raise RuntimeError("Mac resume has no recorded lease")
            self.client = Client(os.environ.get("AIFACTORY_WORKER_DB") or paths.WORKSPACE / "workers" / "queue.sqlite3",
                                 self.project["worker"], lease, self.run_dir)
            if self.resume:
                self.resume_guest(lease)
                return
            # A direct MCP run may wait for first-time image preparation. Dispatch
            # skips an unready worker, so a downloading Mac does not stall its queue.
            deadline = time.monotonic() + int(os.environ.get("AIFACTORY_MAC_PREPARE_WAIT_S", "21600"))
            self.set_current("prepare", "code", "prepare.log")
            while True:
                available = next((w for w in self.client.store.workers() if w["id"] == self.project["worker"]), {})
                if not available.get("online") or not available.get("info", {}).get("lifecycle"):
                    raise RuntimeError("Mac worker is offline or not configured for lifecycle operations")
                network_ready = available["info"].get("network_ready") is not False
                if available["info"].get("base_ready") is not False and network_ready: break
                reason = "Mac base image" if network_ready else "Mac network setup (Softnet root/SUID)"
                message = f"waiting for the {reason}; no VM or lease allocated yet"
                self.log(message)
                with (self.run_dir / "prepare.log").open("a") as f: f.write(message + "\n")
                if time.monotonic() > deadline: raise TimeoutError("Mac worker preparation timed out")
                time.sleep(30)
            self.client.store.acquire(self.project["worker"], lease)
            self.state["lease"] = lease; self.save()
            self.log("Mac VM prepare")
            _, r = self.client.execute("guest-prepare")
            if r.returncode: raise RuntimeError("Mac VM prepare failed; lease retained")
            self.setup_project()

        def resume_guest(self, lease):
            owned = next((w for w in self.client.store.workers() if w["id"] == self.project["worker"]), {})
            if (owned.get("lease") or {}).get("id") != lease:
                raise RuntimeError("Mac resume requires this run's retained lease")
            ready = self.sb(f"test -d \"$SANDBOX_APP_DIR/.git\" && test -s {shlex.quote(self.work + '/ticket.md')} && printf prepared", check=False)
            if ready == "prepared":
                self.refresh_token()
                return
            # Explicit resume can retry provisioning only before credentials or
            # a repository were created, in the same running, owned guest.
            clean = self.sb(f"test ! -e \"$SANDBOX_APP_DIR\" && test ! -L \"$SANDBOX_APP_DIR\" && test ! -e {shlex.quote(self.env_file)} && printf provisionable", check=False)
            if self.state.get("history") or clean != "provisionable":
                raise RuntimeError("Mac setup is incomplete or the guest is stopped; inspect the retained guest before recovery")
            self.log("resume provisioning in the retained Mac VM")
            self.setup_project()

        def setup_project(self):
            self.sb(f"mkdir -p {shlex.quote(self.work)}")
            provision = self.project_dir / "provision.sh"
            if provision.exists():
                remote = self.work + "/provision.sh"
                self.scp_to(provision, remote)
                self.set_current("prepare", "code", "code-prepare.log")
                rc, out = self.run_remote(f"bash {shlex.quote(remote)}", self.run_dir / "code-prepare.log")
                if rc: raise RuntimeError(f"Mac project provisioning failed ({rc}): {out[-1000:]}")
            self.refresh_token()
            app = shlex.quote(self.project["app_dir"])
            repo = shlex.quote("https://github.com/" + self.project["repo"] + ".git")
            self.sb(f"test ! -e {app} && gh auth setup-git && git clone -q {repo} {app}")
            ref = self.branch if self.pr_number else self.base
            self.sb(f"cd {app} && git fetch -q origin {shlex.quote(ref)} && git checkout -q -b {shlex.quote(self.branch)} origin/{shlex.quote(ref)} && git config user.name 'aifactory' && git config user.email 'aifactory@users.noreply.github.com'")
            self.sb(f"cat > {shlex.quote(self.work + '/ticket.md')}", input_text=self.ticket)
            self.log("Mac VM ready")

        def refresh_token(self):
            if self.dry: return
            r = subprocess.run([str(ROOT / "sandbox" / "bin" / "sandbox"), "gh-app", "token", self.pj], text=True, capture_output=True)
            token = r.stdout.strip()
            if r.returncode or not token.startswith("ghs_") or "\n" in token:
                raise RuntimeError("cannot mint project-scoped GitHub token")
            cfg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config") / "sandbox"
            # These are existing, administrator-owned sandbox credential files.
            files = [cfg / "env", cfg / "pj" / (self.pj + ".env")]
            script = "set -a\n" + "\n".join(f"test ! -f {shlex.quote(str(p))} || source {shlex.quote(str(p))}" for p in files)
            script += "\nprintf '%s' \"${CLAUDE_CODE_OAUTH_TOKEN:-}\""
            r = subprocess.run(["bash", "-c", script], text=True, capture_output=True)
            oauth = r.stdout.strip()
            if r.returncode or not oauth: raise RuntimeError("Claude OAuth token not configured for project")
            data = f"export GH_TOKEN={shlex.quote(token)}\nexport CLAUDE_CODE_OAUTH_TOKEN={shlex.quote(oauth)}\n"
            # stdin is a private, transient spool file; never in command, ticket, operation_show or logs.
            self.sb(f"umask 077; cat > {shlex.quote(self.env_file)}", input_text=data)

        def preserve(self):
            if self.dry: return ""
            # Preserve on the control plane, without overwriting any remote branch.
            patch = self.sb(f"cd $SANDBOX_APP_DIR && git diff --binary origin/{shlex.quote(self.base)}", check=False)
            if patch: (self.run_dir / "wip.patch").write_text(patch)
            return ""

        def run_code(self, step):
            if self.dry: return True, "(dry-run)"
            name = step["code"]
            log_path = self.run_dir / f"code-{step['id']}-{len(self.state['history'])}.log"
            self.set_current(step["id"], "code", log_path.name)
            if name == "gates.sh":
                remote = self.work + "/gates.sh"
                self.scp_to(self.project_dir / self.project["gates"], remote)
                rc, out = self.run_remote(f"cd $SANDBOX_APP_DIR && BASE={shlex.quote(self.base)} bash {shlex.quote(remote)}", log_path)
                # Never turn a nonzero exit without a FAIL line into success.
                self.sb(f"cat > {shlex.quote(self.work + '/gates.txt')}", input_text=out)
                return rc == 0 and not re.search(r"^FAIL(?:\s|$)", out, re.M), out[-4000:]
            if name != "pr-create.sh":
                return False, f"Mac backend does not support code step {name}"
            self.refresh_token()
            commits = self.sb(f"cd $SANDBOX_APP_DIR && git log --oneline origin/{shlex.quote(self.base)}..HEAD").strip()
            if not commits: return False, "no commits to publish"
            parts = ["aifactoryのmacOS VMで実装・検証した変更です。", ""]
            for file in ("report.md", "review.md", "gates.txt"):
                value = self.vm_read(file).strip()
                if value: parts += ["## " + file, "", value, ""]
            body = "\n".join(parts)
            self.sb(f"cat > {shlex.quote(self.work + '/pr-body.md')}", input_text=body)
            cmd = (f"cd $SANDBOX_APP_DIR && git push -q -u origin {shlex.quote(self.branch)} && "
                   f"gh pr create --base {shlex.quote(self.base)} --head {shlex.quote(self.branch)} "
                   f"--title {shlex.quote(self.title)} --body-file {shlex.quote(self.work + '/pr-body.md')}")
            rc, out = self.run_remote(cmd, log_path)
            urls = re.findall(r"https://github\.com/" + re.escape(self.project["repo"]) + r"/pull/\d+", out)
            if rc or not urls: return False, out[-4000:]
            self.sb(f"cat > {shlex.quote(self.work + '/pr_url')}", input_text=urls[-1] + "\n")
            return True, urls[-1]

        def release(self):
            if self.dry: return
            # Transfer only direct regular workflow artifacts, excluding credentials.
            # No archive extraction, symlink following, or arbitrary host destination.
            script = '''import pathlib,sys,base64,hashlib,json,os,stat
p=pathlib.Path(sys.argv[1]); out={}; total=0
for f in p.iterdir():
 if f.name == 'runtime.env': continue
 if f.is_symlink() or not f.is_file(): raise RuntimeError('non-regular artifact')
 fd=os.open(f,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as source:
  if not stat.S_ISREG(os.fstat(source.fileno()).st_mode): raise RuntimeError('non-regular artifact')
  b=source.read(4*1024*1024-total+1)
 total+=len(b)
 if total>4*1024*1024: raise RuntimeError('artifact limit exceeded')
 out[f.name]={'data':base64.b64encode(b).decode(),'sha256':hashlib.sha256(b).hexdigest()}
print(json.dumps(out))'''
            raw = self.sb(f"python3 -c {shlex.quote(script)} {shlex.quote(self.work)}")
            files = json.loads(raw)
            if not isinstance(files, dict) or len(files) > 128:
                raise RuntimeError("invalid artifact manifest; lease retained")
            dest = self.run_dir / "work"
            if dest.is_symlink(): raise RuntimeError("artifact directory is a symlink")
            dest.mkdir(exist_ok=True)
            total = 0
            for name, item in files.items():
                if pathlib.PurePosixPath(name).name != name or name in (".", "..", "runtime.env") or "\\" in name or len(name)>255:
                    raise RuntimeError("unsafe artifact name; lease retained")
                content = base64.b64decode(item["data"], validate=True)
                total += len(content)
                if total > 4*1024*1024: raise RuntimeError("artifact limit exceeded; lease retained")
                if hashlib.sha256(content).hexdigest() != item["sha256"]:
                    raise RuntimeError("artifact checksum mismatch; lease retained")
                if (dest/name).is_symlink(): raise RuntimeError("artifact destination is a symlink")
                with tempfile.NamedTemporaryFile(dir=dest, delete=False) as f:
                    f.write(content); f.flush(); os.fsync(f.fileno())
                    tmp = f.name
                os.replace(tmp, dest/name)
            (self.run_dir / "artifacts.json").write_text(json.dumps({k:v["sha256"] for k,v in files.items()}, indent=2))
            self.state["artifacts_received"] = True; self.save()
            if self.keep:
                self.log("Mac lease retained (--keep)"); return
            op, r = self.client.execute("guest-release")
            if r.returncode: raise RuntimeError("Mac release failed; lease retained")
            self.client.store.release_lease(self.project["worker"], self.client.lease, op)
            self.state["released"] = True; self.save()
            self.log("Mac VM released after artifact verification")
    return MacRun
