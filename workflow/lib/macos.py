"""macOS pull backend for the shared workflow state machine.

Guest commands, inputs and artifacts travel via the durable worker queue.
No control-plane SSH/SCP connection to a Mac is used.
"""
import base64
import datetime
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


# 回収するのは作業ディレクトリ直下の通常ファイルだけ。ディレクトリ・symlink・上限超過は
# 例外にせず飛ばして名前を返す。回収対象外があることは回収の失敗ではなく、ここで止めると
# guest-release まで届かず lease が Mac を塞ぐ（チケット 277）。
# アーカイブ展開・symlink 追従・任意のホスト宛先は行わない。
SKIP_REASONS = ("directory", "symlink", "non-regular", "size")
COLLECT_SCRIPT = '''import pathlib,sys,base64,hashlib,json,os,stat
LIMIT=4*1024*1024
p=pathlib.Path(sys.argv[1]); out={}; skipped=[]; total=0
def skip(f,reason): skipped.append({'name':f.name,'reason':reason})
for f in sorted(p.iterdir()):
 if f.name == 'runtime.env': continue
 if f.is_symlink(): skip(f,'symlink'); continue
 if not f.is_file(): skip(f,'directory' if f.is_dir() else 'non-regular'); continue
 fd=os.open(f,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as source:
  if not stat.S_ISREG(os.fstat(source.fileno()).st_mode): skip(f,'non-regular'); continue
  b=source.read(LIMIT-total+1)
 if len(b)>LIMIT-total: skip(f,'size'); continue
 total+=len(b)
 out[f.name]={'data':base64.b64encode(b).decode(),'sha256':hashlib.sha256(b).hexdigest()}
print(json.dumps({'files':out,'skipped':skipped}))'''


# ---- worker の取り合い（チケット 373）
# pull worker は PJ を跨いで共有する 1 台なので、「他 run が使っている」は Proxmox のプール満杯と同じ
# 「待てば解ける失敗」。PoolBusy に寄せて runner の take_waiting（--wait）に待たせる。
# offline / lifecycle 無しは設定・稼働の問題で待っても直らないので、従来どおり即失敗のまま。
LEASE_BUSY = ("leased to another run", "worker busy")

# `sandbox keys pick` が「要る用途の Claude の鍵が鍵プールに無い」と言うときの目印（sandbox/bin/sandbox の die 文言。
# workflow/bin/run の NO_KEY と同じ。ADR-0046）。これを見た工程は失敗ではなく一時停止にする
NO_KEY = "鍵なし:"


def _since(created):
    """lease を取った時刻（epoch）を ISO に。読めなければ None"""
    try: n = float(created)
    except (TypeError, ValueError): return None
    if n <= 0: return None
    return datetime.datetime.fromtimestamp(n).astimezone().isoformat(timespec="seconds")


def holder_run(run, lease_id):
    """lease を持っている run の名前（runs/<run>/state.json の lease で引く）。分からなければ lease id をそのまま"""
    try:
        for f in sorted(run.run_dir.parent.glob("*/state.json")):
            try:
                if json.loads(f.read_text(encoding="utf-8")).get("lease") == lease_id: return f.parent.name
            except (OSError, ValueError): continue
    except OSError: pass
    return lease_id


def busy_reason(run, worker, lease):
    """worker を他 run が握っているなら、その 1 行（誰がいつから）。空いていれば None"""
    worker_id = run.project["worker"]
    held = (worker.get("lease") or {}).get("id")
    if held and held != lease:
        since = _since((worker.get("lease") or {}).get("created"))
        return f"{worker_id} は {holder_run(run, held)} が使用中" + (f"（{since}）" if since else "")
    if not held:
        op = worker.get("operation") or {}
        # 前の run の後始末（queued / running / uncertain）が残っている。これも待てば解ける
        if op.get("id"): return f"{worker_id} は前の操作（{op['id']} / {op.get('state')}）の終了待ち"
    return None


def pool_busy(run, reason):
    """待てる失敗として上げる例外を作る（例外の種類は runner が Run.PoolBusy で渡してくる）"""
    cls = getattr(run, "PoolBusy", None)
    return cls(reason, reason=reason) if cls else RuntimeError(reason)


def no_key(run, reason):
    """鍵プールから鍵が取れなかったときに上げる例外（種類は runner が Run.NoKey で渡してくる）"""
    cls = getattr(run, "NoKey", None)
    return cls(reason) if cls else RuntimeError(reason)


def acquire_lease(run, worker, lease):
    """worker の lease を取る。他 run が使っている間は PoolBusy（--wait なら待ち直す）にする（チケット 373）"""
    reason = busy_reason(run, worker, lease)
    if reason: raise pool_busy(run, reason)
    try:
        run.client.store.acquire(run.project["worker"], lease)
    except Exception as e:
        # 見てから取るまでの間に他 run が入った（レース）。queue の 409 文言だけが手掛かりなので、
        # 待てる失敗（lease / 操作の競合）に限って包み直す。他の 409（offline・base 未準備）はそのまま
        if any(m in str(e) for m in LEASE_BUSY):
            raise pool_busy(run, busy_reason(run, worker, lease) or f"{run.project['worker']} は他の run が使用中") from e
        raise


def backend(Run):
    class MacRun(Run):
        backend_label = "Mac VM"
        # pull backend が kit/workflows/*.yml の code step をどう扱うかの対応表。分類は 3 つ:
        #   run         … この backend が実装している
        #   noop        … 対応しないが素通りさせる（True を返す。Windows の sync-base）
        #   unsupported … 起動前に拒否する（実装が無いまま黙って PR を作らずに終わらせない）
        # code step を足す人は kit/steps/ に置くだけでなく 3 つの pull backend（macos / windows / linux）の
        # この表も更新すること。忘れると workflow/tests/test_code_steps.py が赤くなる（チケット 386 / asura #381）
        CODE_STEPS = {"gates.sh": "run", "sync-base": "run", "pr-create.sh": "run",
                      "pr-automerge.sh": "run", "pr-merge.sh": "unsupported"}

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # auto_merge の無い PJ では runner が automerge 工程を飛ばす（ADR-0042）ので、未対応の判定からも外す
            skipped = set() if getattr(self, "auto_merge", None) else set(getattr(Run, "SKIPPABLE_CODE_STEPS", ("pr-automerge.sh",)))
            codes = {s["code"] for s in self.wf["steps"] if "code" in s} - skipped
            # 表に無い step（足した人が対応表を更新していない）は unsupported と同じ扱いにする
            unsupported = {c for c in codes if self.CODE_STEPS.get(c, "unsupported") == "unsupported"}
            if unsupported: raise ValueError("unsupported pull-worker code steps: " + ", ".join(sorted(unsupported)))
            self.work = str(pathlib.PurePosixPath(self.project["app_dir"]).parent / "work" / self.task)
            self.env_file = str(pathlib.PurePosixPath(self.work) / "runtime.env")
            self.client = None
            self.run_lock = None
            self.lease_id = None
            self.state["backend"] = "macos-pull"
            self.state["worker"] = self.project["worker"]
            # `--resume` で前回の終わり方（result / finished / error …）を消すのは Run.__init__ に寄せた（チケット 338）

        def main(self):
            try:
                return super().main()
            except Exception as e:
                import datetime
                self.state.update(result="human", error=str(e),
                                  finished=datetime.datetime.now().isoformat(timespec="seconds"))
                self.save()
                self.log(f"{self.backend_label} execution failed: {e}; existing lease retained for inspection")
                return 2

        def run_agent(self, step, retry_note=""):
            cls = getattr(self, "NoKey", ())
            try:
                self.refresh_token()
            except cls as e:
                # 要る用途の鍵が鍵プールから取れない。その step が悪いわけではないので、戻しの回数を消費せず
                # 未コミットの変更を wip に保全して人へ返す（利用枠切れと同じ扱い。PAUSE_KINDS。チケット 391 / 380）
                self.log(f"agent key: {step['id']} は鍵が使えずで止まった。未コミットの変更を wip として保全する")
                self.commit_tracked(getattr(self, "WIP_KEY_MESSAGE", "wip: token unusable"))
                self.last_fail = {"failure": "key", "reason": str(e)}
                return False, f"agent key: {e}"
            self.configure_computer()
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

        def record_needed_keys(self):
            """鍵待ちで止まったときに「どの用途の鍵を待っているか」を kb が読む（ADR-0046）。prepare より前に残す。
            windows / linux の take() は これを継承せず丸ごと上書きしているので、3 つの take() から呼ぶ（391）"""
            self.state["needed_keys"] = self.needed_keys(); self.save()

        def take(self):
            if self.dry: return
            self.record_needed_keys()
            import aifactory_paths as paths
            # --wait のとき take は呼び直される（373）。lock と lease id と Client は最初の 1 回だけ作る
            # （呼ぶたびに同じファイルを開き直すと、同じプロセスの別 fd への flock で必ず落ちる）
            if self.run_lock is None:
                self.run_lock = open(self.run_dir / "macos.lock", "a")
                fcntl.flock(self.run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.client is None:
                lease = self.state.get("lease") if self.resume else f"run-{self.task}-{uuid.uuid4().hex[:16]}"
                if not lease: raise RuntimeError("Mac resume has no recorded lease")
                self.lease_id = lease
                self.client = Client(os.environ.get("AIFACTORY_WORKER_DB") or paths.WORKSPACE / "workers" / "queue.sqlite3",
                                     self.project["worker"], lease, self.run_dir)
            lease = self.lease_id
            if self.resume:
                self.resume_guest(lease)
                return
            # A direct MCP run may wait for first-time image preparation. Dispatch
            # skips an unready worker, so a downloading Mac does not stall its queue.
            deadline = time.monotonic() + int(os.environ.get("AIFACTORY_MAC_PREPARE_WAIT_S", "21600"))
            prepared = False
            while True:
                available = next((w for w in self.client.store.workers() if w["id"] == self.project["worker"]), {})
                if not available.get("online") or not available.get("info", {}).get("lifecycle"):
                    raise RuntimeError("Mac worker is offline or not configured for lifecycle operations")
                # 他 run が使っている間は待てる失敗として上げる。set_current より前に見て、
                # 待ちの表示（wait-vm）を prepare で上書きしない（373）
                busy = busy_reason(self, available, lease)
                if busy: raise pool_busy(self, busy)
                network_ready = available["info"].get("network_ready") is not False
                if available["info"].get("base_ready") is not False and network_ready: break
                if not prepared:
                    self.set_current("prepare", "code", "prepare.log"); prepared = True
                reason = "Mac base image" if network_ready else "Mac network setup (Softnet root/SUID)"
                message = f"waiting for the {reason}; no VM or lease allocated yet"
                self.log(message)
                with (self.run_dir / "prepare.log").open("a") as f: f.write(message + "\n")
                if time.monotonic() > deadline: raise TimeoutError("Mac worker preparation timed out")
                time.sleep(30)
            self.set_current("prepare", "code", "prepare.log")
            acquire_lease(self, available, lease)
            self.state["lease"] = lease; self.save()
            self.log("Mac VM prepare")
            # display \u3092\u66f8\u3044\u305f PJ \u3060\u3051\u89e3\u50cf\u5ea6\u3092\u6e21\u3059\u3002\u7121\u3051\u308c\u3070\u5f15\u6570\u306a\u3057\u3067\u5f93\u6765\u3069\u304a\u308a\uff08343\uff09
            display = self.project.get("display") or {}
            prepare = {"width": display["width"], "height": display["height"]} if display else {}
            _, r = self.client.execute("guest-prepare", prepare)
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
            self.log(f"{self.backend_label} ready")

        def configure_computer(self):
            if not self.project.get('computer_use') or self.dry: return
            executable = (r'C:\ProgramData\AIFactoryWorker\bin\aifactory-computer.exe' if self.state['backend']=='windows-pull'
                          else '/usr/local/lib/aifactory-computer/aifactory-computer' if self.state['backend']=='linux-pull'
                          else str(pathlib.PurePosixPath(self.project['app_dir']).parent / '.local/lib/aifactory-computer/aifactory-computer'))
            config = {'mcpServers': {'computer': {'command': executable, 'args': ['-mode', 'mcp', '-artifacts', self.work]}}}
            local = self.run_dir / 'computer-mcp.json'
            local.write_text(json.dumps(config), encoding='utf-8')
            self.scp_to(local, self.work + '/computer-mcp.json')

        def credentials(self):
            r = subprocess.run([str(ROOT / "sandbox" / "bin" / "sandbox"), "gh-app", "token", self.pj], text=True, capture_output=True)
            token = r.stdout.strip()
            if r.returncode or not token.startswith("ghs_") or "\n" in token:
                raise RuntimeError("cannot mint project-scoped GitHub token")
            return {"GH_TOKEN": token, **self.claude_credentials()}

        def claude_credentials(self):
            """Claude の鍵は制御系の鍵プール（keys.json）が正本。Proxmox の take と同じ規則で系統ごとに選ぶ（ADR-0044 / ADR-0046）。

            以前はここで `~/.config/sandbox/env` と `pj/<pj>.env` を source して `${CLAUDE_CODE_OAUTH_TOKEN}` を読んでいたので、
            プール運用でファイルから鍵を消してあると **runner のプロセス環境に残っていた古い鍵**に落ちていた
            （無効化した鍵が全工程・全モデルで使われ続けた。2026-09-10）。選び方は sandbox の `keys pick` 1 か所に置く。
            stdout は鍵の値そのものなので、ログにも例外文にも state にも入れない（残すのは選ばれた鍵の名前だけ）"""
            cur = ",".join(f"{g}={n}" for g, n in sorted((self.state.get("keys") or {}).items()) if n)
            cmd = [str(ROOT / "sandbox" / "bin" / "sandbox"), "keys", "pick", "--pj", self.pj, "--task", str(self.task),
                   "--need=" + ",".join(self.needed_keys()), "--json"]
            if cur: cmd.append("--current=" + cur)
            r = subprocess.run(cmd, text=True, capture_output=True)
            if r.returncode:
                why = (r.stderr or "").strip().splitlines()
                if any(NO_KEY in l for l in why):
                    raise no_key(self, ([l.strip() for l in why if NO_KEY in l])[-1][:300])
                raise RuntimeError(f"cannot pick a Claude key from the pool ({r.returncode}): {(why or [''])[-1][:300]}")
            try:
                picked = json.loads(r.stdout)
                values = {str(k): str(v) for k, v in (picked["env"] or {}).items()}
            except (KeyError, TypeError, ValueError):
                raise RuntimeError("sandbox keys pick did not return the expected JSON") from None   # stdout は鍵なので出さない
            if not values: raise RuntimeError("sandbox keys pick returned no Claude key")
            self.state["keys"] = picked.get("keys") or {}   # 次の工程の --current（同じ鍵を使い続ける）と、人が読む記録。名前だけ
            self.save()
            return values

        def refresh_token(self):
            if self.dry: return
            values = self.credentials()
            data = "".join(f"export {key}={shlex.quote(value)}\n" for key, value in values.items())
            # stdin is a private, transient spool file; never in command, ticket, operation_show or logs.
            self.sb(f"umask 077; cat > {shlex.quote(self.env_file)}", input_text=data)

        def preserve(self):
            if self.dry: return ""
            # 保全そのものは Proxmox backend と同じ（作業ブランチの HEAD を wip ブランチへ、駄目なら wip.patch）。
            # ただし失敗しても例外を外に出さない: MacRun.main の except に抜けると release（成果物回収・
            # ゲスト削除）まで届かず、実装も lease も取り残される（チケット 282）
            try:
                return super().preserve()
            except Exception as e:
                self.log(f"{self.backend_label} preserve failed: {str(e)[-200:]}")
                return ""

        def run_code(self, step):
            if self.dry: return True, "(dry-run)"
            name = step["code"]
            # sync-base は runner 内蔵で、POSIX の git しか使わないので本体の実装がそのまま動く（チケット 239）
            if name == "sync-base": return self.run_sync(step)
            log_path = self.run_dir / f"code-{step['id']}-{len(self.state['history'])}.log"
            self.set_current(step["id"], "code", log_path.name)
            if name == "gates.sh":
                remote = self.work + "/gates.sh"
                self.scp_to(self.project_dir / self.project["gates"], remote)
                rc, out = self.run_remote(f"cd $SANDBOX_APP_DIR && BASE={shlex.quote(self.base)} bash {shlex.quote(remote)}", log_path)
                # Never turn a nonzero exit without a FAIL line into success.
                self.sb(f"cat > {shlex.quote(self.work + '/gates.txt')}", input_text=out)
                return rc == 0 and not re.search(r"^FAIL(?:\s|$)", out, re.M), out[-4000:]
            if name == "pr-automerge.sh": return self.run_automerge(log_path)
            if name != "pr-create.sh":
                return False, f"Mac backend does not support code step {name}"
            self.refresh_token()
            commits = self.sb(f"cd $SANDBOX_APP_DIR && git log --oneline origin/{shlex.quote(self.base)}..HEAD").strip()
            if not commits: return False, "no commits to publish"
            parts = [f"aifactoryの{self.backend_label}で実装・検証した変更です。", ""]
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

        def run_automerge(self, log_path):
            """automerge（ADR-0042）を guest の中で走らせる（チケット 386）。

            pull worker には制御系から入る `sandbox ssh` が無いので、kit/steps/pr-automerge.sh を guest の $WORK に
            置き、SB_LOCAL=1 で「guest の中の自分」に対して走らせる。判定に使う pr_url / gates.txt / review.md は
            回収前の guest の $WORK にあるのでそのまま読め、gh は runtime.env の GH_TOKEN（PJ 限定の App token）で動く。
            CI 待ちのポーリングも guest の中で回るので、guest-exec は待ち時間ぶん長く張る（上限は run_remote の 3600 秒）"""
            self.refresh_token()
            remote = self.work + "/pr-automerge.sh"
            self.scp_to(ROOT / "workflow" / "kit" / "steps" / "pr-automerge.sh", remote)
            env = {"SB_LOCAL": "1", "TASK": str(self.task), "WORK": self.work,
                   "BASE": self.base, "BRANCH": self.branch, **self.automerge_env()}
            prefix = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in sorted(env.items()))
            wait_min = int((self.auto_merge or {}).get("wait_min", 20) or 20)
            # CI 待ち（wait_min）+ マージと後始末の余裕。上限に当たった回は NOMERGE と同じ「PR を開いたまま人へ」で終わる
            rc, out = self.run_remote(f"cd $SANDBOX_APP_DIR && {prefix} bash {shlex.quote(remote)}", log_path,
                                      timeout=min(3600, wait_min * 60 + 900))
            # Proxmox backend では bin/run の run_code が呼ぶ。pull backend はそこを通らないので自分で呼ぶ
            # （忘れると実際にマージしても state.json に merged が入らず、kb がチケットを done にしない）
            self.note_merged()
            return rc == 0, out[-4000:]

        def collect(self):
            return self.sb(f"python3 -c {shlex.quote(COLLECT_SCRIPT)} {shlex.quote(self.work)}")

        def release(self):
            if self.dry: return
            manifest = json.loads(self.collect())
            if not isinstance(manifest, dict) or "files" not in manifest:
                raise RuntimeError("invalid artifact manifest; lease retained")
            self.accept_artifacts(manifest["files"], manifest.get("skipped", []))

        def record_skipped(self, skipped):
            # ゲスト側の名前は agent が作れるので、件数・長さ・理由を丸めてから state に入れる。
            # PowerShell の ConvertTo-Json は 1 件の配列を単体に潰すことがあるので dict も受ける。
            if isinstance(skipped, dict): skipped = [skipped]
            clean = []
            for item in skipped if isinstance(skipped, (list, tuple)) else []:
                if len(clean) >= 128: break
                if not isinstance(item, dict): continue
                reason = item.get("reason")
                entry = {"name": str(item.get("name", ""))[:255],
                         "reason": reason if reason in SKIP_REASONS else "other"}
                clean.append(entry)
                self.log(f"{self.backend_label} artifact skipped: {entry['name']} ({entry['reason']})")
            if clean: self.state["artifacts_skipped"] = clean

        def accept_artifacts(self, files, skipped=()):
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
            self.state["artifacts_received"] = True
            self.record_skipped(skipped)
            self.save()
            if self.keep:
                self.log(f"{self.backend_label} lease retained (--keep)"); return
            op, r = self.client.execute("guest-release")
            if r.returncode: raise RuntimeError(f"{self.backend_label} release failed; lease retained")
            self.client.store.release_lease(self.project["worker"], self.client.lease, op)
            self.state["released"] = True; self.save()
            self.log(f"{self.backend_label} released after artifact verification")
    return MacRun
