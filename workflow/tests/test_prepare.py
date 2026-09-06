"""project.yml の `prepare`（貸出直後の準備）が take の直後に 1 回だけ走ること（チケット 330）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。PATH の先頭に偽の `sandbox` と `scp` と `claude` を置いて runner を回す
（`test_take_failure.py` と同じ流儀）。偽 sandbox は VM の `/home/dev` を一時 dir に読み替えて `bash -c` で回すので、
checkout も prepare もローカルの本物の git / bash がそのまま動く。

- prepare が非 0 → 工程を 1 つも始めずに failed（failure: prepare）で終わり、agent（claude）を起動しない
- prepare が 0 → そのまま最初の agent 工程に入る
- project.yml に prepare が無い PJ → 準備は走らない（code-prepare.log を作らない）
- kb run 経由なら、チケットは実行中のまま残らず blocked になり、note に prepare と書いてある
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
RUNNER = REPO / "workflow" / "bin" / "run"
KB = REPO / "kanban" / "bin" / "kb"
TICKET = "# 調査: prepare の再現\n\n偽の VM で貸出直後の準備を走らせる。\n"

# VM の /home/dev を $VMROOT に読み替える偽 sandbox。ssh は受け取ったコマンドをそのまま bash で回す
FAKE_SANDBOX = r"""#!/usr/bin/env bash
echo "$@" >> "$CALLS"
case "$1" in
  take) echo "take: sb-t-$2-01 10.77.1.1" ;;
  ssh)  shift 2; cmd="${1//\/home\/dev/$VMROOT}"
        SANDBOX_APP_DIR="$VMROOT/app" HOME="$VMROOT" exec bash -c "$cmd" ;;
esac
exit 0
"""

# scp の宛先 dev@<ip>:<VM のパス> を $VMROOT 配下に写す
FAKE_SCP = r"""#!/usr/bin/env bash
src="${@: -2:1}"; dst="${@: -1}"; dst="${dst#*:}"; dst="$VMROOT${dst#/home/dev}"
mkdir -p "$(dirname "$dst")" && cp "$src" "$dst"
"""

FAKE_CLAUDE = r"""#!/usr/bin/env bash
echo started > "$VMROOT/claude-ran"
exit 0
"""

PROJECT = """name: {pj}
repo: example/{pj}
base_branch: develop
app_dir: {app}
gates: gates.sh
"""


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if p.returncode: raise AssertionError(f"git {' '.join(args)} @ {cwd}: {p.stdout}{p.stderr}")
    return p.stdout


class PrepareTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.vm = self.ws / "vm"; (self.vm / "work").mkdir(parents=True)
        self.bin = self.ws / "bin"; self.bin.mkdir()
        for name, body in (("sandbox", FAKE_SANDBOX), ("scp", FAKE_SCP), ("claude", FAKE_CLAUDE)):
            f = self.bin / name; f.write_text(body, encoding="utf-8"); f.chmod(0o755)
        self.ticket = self.ws / "ticket.md"; self.ticket.write_text(TICKET, encoding="utf-8")
        self.calls = self.ws / "calls.log"; self.calls.write_text("")
        self.sbstate = self.ws / "sandbox-state.json"
        self.sbstate.write_text(json.dumps({str(t): {"ip": "10.77.1.1"} for t in range(900, 910)}), encoding="utf-8")
        # 偽 VM の中のリポジトリ（take の checkout は本物の git が走る）
        origin = self.ws / "origin.git"
        git(self.ws, "init", "-q", "--bare", "-b", "develop", str(origin))
        seed = self.ws / "seed"; seed.mkdir()
        git(seed, "init", "-q", "-b", "develop")
        git(seed, "config", "user.name", "aifactory test"); git(seed, "config", "user.email", "test@example.invalid")
        (seed / "README.md").write_text("pj\n", encoding="utf-8")
        git(seed, "add", "-A"); git(seed, "commit", "-q", "-m", "初期")
        git(seed, "remote", "add", "origin", str(origin)); git(seed, "push", "-q", "-u", "origin", "develop")
        self.app = self.vm / "app"
        git(self.ws, "clone", "-q", str(origin), str(self.app))
        git(self.app, "config", "user.name", "aifactory test"); git(self.app, "config", "user.email", "test@example.invalid")

    def project(self, pj, prepare=None):
        d = self.ws / "projects" / pj; d.mkdir(parents=True, exist_ok=True)
        y = PROJECT.format(pj=pj, app=self.app)
        if prepare is not None:
            y += "prepare: prepare.sh\n"
            (d / "prepare.sh").write_text(prepare, encoding="utf-8")
        (d / "project.yml").write_text(y, encoding="utf-8")
        (d / "gates.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        return d

    def env(self, **extra):
        return dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", AIFACTORY_WORKSPACE=str(self.ws),
                    VMROOT=str(self.vm), CALLS=str(self.calls), SANDBOX_STATE=str(self.sbstate), **extra)

    def run_runner(self, pj, task):
        import datetime
        name = f"{datetime.date.today().isoformat()}-{pj}-{task}"
        p = subprocess.run([sys.executable, str(RUNNER), pj, task, "research", str(self.ticket)],
                           text=True, capture_output=True, env=self.env())
        return p, self.ws / "runs" / name

    def state(self, run_dir):
        f = run_dir / "state.json"
        self.assertTrue(f.exists(), f"state.json が無い: {sorted(p.name for p in run_dir.iterdir())}")
        return json.loads(f.read_text(encoding="utf-8"))

    # ---------- a: 準備が失敗したら agent を起こさずに終わる
    def test_failing_prepare_stops_the_run_before_any_agent_starts(self):
        self.project("prepfail", prepare='#!/usr/bin/env bash\necho "migration が当たりません"\nexit 1\n')
        p, run_dir = self.run_runner("prepfail", "901")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        s = self.state(run_dir)
        self.assertEqual(s["result"], "failed")
        self.assertEqual(s["failure"], "prepare")
        self.assertEqual(s["history"], [])            # 工程は 1 つも走っていない
        self.assertIsNone(s["current"])
        self.assertEqual(s["next"], "human")
        self.assertIn("prepare", s["error"])
        self.assertIn("migration が当たりません", s["error"])
        self.assertNotIn("waited_s", s)               # 待っていない終わり方に「0 秒待った」と書かない
        # 準備の出力は run に残る（人がここを読んで VM か prepare.sh を直す）
        log = (run_dir / "code-prepare.log").read_text(encoding="utf-8")
        self.assertIn("migration が当たりません", log)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertNotIn("claude", calls)             # VM 時間を焼かない
        self.assertIn("release 901", calls)           # 貸した VM は返す
        self.assertFalse((self.vm / "claude-ran").exists())

    # ---------- b: 準備が通れば、そのまま最初の工程に入る
    def test_successful_prepare_runs_once_and_the_first_step_starts(self):
        self.project("prepok", prepare='#!/usr/bin/env bash\ntouch "$HOME/prepared"\necho ok\n')
        p, run_dir = self.run_runner("prepok", "902")
        s = self.state(run_dir)
        self.assertNotEqual(s.get("failure"), "prepare")
        self.assertIn("ok", (run_dir / "code-prepare.log").read_text(encoding="utf-8"))
        self.assertTrue((self.vm / "prepared").exists())        # app_dir を cwd に VM の中で 1 回走った
        self.assertTrue((self.vm / "claude-ran").exists(), p.stdout[-2000:])
        self.assertEqual(self.calls.read_text(encoding="utf-8").count("prepare.sh"), 1)

    # ---------- c: prepare を書いていない PJ は今までどおり
    def test_a_project_without_prepare_runs_nothing_extra(self):
        self.project("noprep")
        p, run_dir = self.run_runner("noprep", "903")
        self.assertFalse((run_dir / "code-prepare.log").exists())
        self.assertNotIn("prepare.sh", self.calls.read_text(encoding="utf-8"))
        self.assertTrue((self.vm / "claude-ran").exists(), p.stdout[-2000:])

    # ---------- d: 板の側（kb）でも「準備で止まった」と分かる
    def test_kb_run_blocks_the_ticket_and_says_prepare(self):
        self.project("prepfail", prepare='#!/usr/bin/env bash\nexit 1\n')
        env = self.env()
        new = subprocess.run([sys.executable, str(KB), "new", "prepfail", "research", "prepare の再現",
                              "--body", "-", "--id", "904"], input=TICKET, text=True, capture_output=True, env=env)
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)
        r = subprocess.run([sys.executable, str(KB), "run", "904"], text=True, capture_output=True, env=env)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        show = subprocess.run([sys.executable, str(KB), "show", "904"], text=True, capture_output=True, env=env)
        head = show.stdout.split("-" * 60)[0].splitlines()
        t = {l.split(" ", 1)[0]: l.split(" ", 1)[1].strip() for l in head if l.strip()}
        self.assertEqual(t["status"], "blocked", t)
        self.assertIn("準備（prepare）で止まった", t["note"])
        self.assertNotIn("VM を取得できず", t["note"])   # VM は取れている。直す所は prepare.sh か VM の側


if __name__ == "__main__":
    unittest.main()
