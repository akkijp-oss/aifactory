"""take（VM の取得と準備）が失敗した run の記録とチケット状態（チケット 238）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。PATH の先頭に偽の `sandbox` を置いて runner を回す（workflow/README.md と同じ流儀）。
- take が非 0 → state.json に result: failed と理由が残り、rc=2
- take 成功 → 直後の ssh が非 0 → VM を返してから同じ記録を残す
- kb run 経由ならチケットは in_progress のまま残らず blocked になる
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
TICKET = "# 調査: take 失敗の再現\n\n偽の sandbox で take を失敗させる。\n"

# 引数を calls.log に足してから振る舞う偽 sandbox。take のとき state.json の有無を marker に書く
FAKE = r"""#!/usr/bin/env bash
echo "$@" >> "$CALLS"
case "$1" in
  take)
    ls "$RUNS/$RUN_NAME/state.json" > "$MARKER" 2>&1 || true
    if [ -n "$TAKE_FAILS" ]; then echo "[error] pj=$2 に空きなし" >&2; exit 1; fi
    echo "take: sb-t-$2-01 10.77.1.1"
    ;;
  ssh)
    if [ -n "$SSH_FAILS" ]; then echo "fatal: couldn't find remote ref develop" >&2; exit 128; fi
    ;;
esac
exit 0
"""


class TakeFailureTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.bin = self.ws / "bin"; self.bin.mkdir()
        fake = self.bin / "sandbox"; fake.write_text(FAKE); fake.chmod(0o755)
        (self.ws / "sandbox-state.json").write_text("{}")
        self.ticket = self.ws / "ticket.md"; self.ticket.write_text(TICKET, encoding="utf-8")
        self.calls = self.ws / "calls.log"
        self.marker = self.ws / "marker.txt"

    def env(self, run_name, **extra):
        return dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                    AIFACTORY_WORKSPACE=str(self.ws), SANDBOX_STATE=str(self.ws / "sandbox-state.json"),
                    CALLS=str(self.calls), MARKER=str(self.marker),
                    RUNS=str(self.ws / "runs"), RUN_NAME=run_name, **extra)

    def run_runner(self, task, **extra):
        import datetime
        name = f"{datetime.date.today().isoformat()}-kumitate-{task}"
        p = subprocess.run([sys.executable, str(RUNNER), "kumitate", task, "research", str(self.ticket)],
                           text=True, capture_output=True, env=self.env(name, **extra))
        return p, self.ws / "runs" / name

    def state(self, run_dir):
        f = run_dir / "state.json"
        self.assertTrue(f.exists(), f"state.json が無い: {sorted(p.name for p in run_dir.iterdir())}")
        return json.loads(f.read_text(encoding="utf-8"))

    def assert_failed_record(self, s, reason):
        self.assertEqual(s["result"], "failed")
        self.assertEqual(s["next"], "human")
        self.assertIsNone(s["current"])
        self.assertIn(reason, s["error"])
        self.assertTrue(s.get("finished") and s.get("started"))
        self.assertEqual((s["pj"], s["task"], s["workflow"]), ("kumitate", s["task"], "research"))

    def test_take_failure_is_recorded_and_state_exists_from_the_start(self):
        p, run_dir = self.run_runner("901", TAKE_FAILS="1")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assert_failed_record(self.state(run_dir), "空きなし")
        self.assertNotIn("No such file", self.marker.read_text())   # ticket.md だけの run を作らない
        self.assertNotIn("release 901", self.calls.read_text())     # 貸し出されていないので返さない

    def test_prepare_failure_releases_the_vm_and_is_recorded(self):
        p, run_dir = self.run_runner("902", SSH_FAILS="1")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assert_failed_record(self.state(run_dir), "couldn't find remote ref")
        self.assertIn("release 902", self.calls.read_text())

    def test_kb_run_does_not_leave_the_ticket_in_progress(self):
        env = self.env("unused", TAKE_FAILS="1")
        new = subprocess.run([sys.executable, str(KB), "new", "kumitate", "research", "take 失敗の再現", "--body", "-", "--id", "903"],
                             input=TICKET, text=True, capture_output=True, env=env)
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)
        import datetime
        name = f"{datetime.date.today().isoformat()}-kumitate-903"
        env["RUN_NAME"] = name
        r = subprocess.run([sys.executable, str(KB), "run", "903"], text=True, capture_output=True, env=env)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        show = subprocess.run([sys.executable, str(KB), "show", "903"], text=True, capture_output=True, env=env)
        head = show.stdout.split("-" * 60)[0].splitlines()
        t = {l.split(" ", 1)[0]: l.split(" ", 1)[1].strip() for l in head if l.strip()}
        self.assertEqual(t["status"], "blocked", t)
        self.assertIn("空きなし", t["note"])
        self.assertEqual(t["run"], name)
        self.assert_failed_record(self.state(self.ws / "runs" / name), "空きなし")


if __name__ == "__main__":
    unittest.main()
