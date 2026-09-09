"""take（VM の取得と準備）が失敗した run の記録とチケット状態（チケット 238）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。PATH の先頭に偽の `sandbox` を置いて runner を回す（workflow/README.md と同じ流儀）。
- take が非 0 → state.json に result: failed と理由が残り、rc=2
- take 成功 → 直後の ssh が非 0 → VM を返してから同じ記録を残す
- kb run 経由ならチケットは in_progress のまま残らず blocked になる
"""
import datetime
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
    if [ -n "$NO_KEY" ]; then echo "[error] 鍵なし: Fable に使う鍵が鍵プールに無い（console の「鍵」画面か sandbox keys add で登録すると、止まった run は自動で再開する）" >&2; exit 1; fi
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
        for k in ("started", "finished"):   # 記録の時刻はオフセット付き（ADR-0026。チケット 235）
            self.assertIsNotNone(datetime.datetime.fromisoformat(s[k]).utcoffset(), f"{k}: {s[k]!r}")
        self.assertEqual((s["pj"], s["task"], s["workflow"]), ("kumitate", s["task"], "research"))

    def test_take_failure_is_recorded_and_state_exists_from_the_start(self):
        p, run_dir = self.run_runner("901", TAKE_FAILS="1")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assert_failed_record(self.state(run_dir), "空きなし")
        self.assertNotIn("No such file", self.marker.read_text())   # ticket.md だけの run を作らない
        self.assertNotIn("release 901", self.calls.read_text())     # 貸し出されていないので返さない

    def test_take_passes_the_needed_key_purposes_from_the_workflow(self):
        """research（Sonnet の research + Fable の judge）は fable と other の両方を要る用途として take に渡す（ADR-0046）"""
        p, run_dir = self.run_runner("904", TAKE_FAILS="1")
        self.assertIn("take kumitate 904 --need=fable,other", self.calls.read_text())
        self.assertEqual(self.state(run_dir)["needed_keys"], ["fable", "other"])

    def test_no_key_in_the_pool_pauses_the_run_without_a_vm(self):
        """鍵プールに要る用途の鍵が無い: take が「鍵なし:」で止まり、runner は failure nokey で終わる（VM は取らない）。kb は todo に戻す"""
        p, run_dir = self.run_runner("905", NO_KEY="1")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        s = self.state(run_dir)
        self.assert_failed_record(s, "鍵なし")
        self.assertEqual(s["failure"], "nokey"); self.assertEqual(s["needed_keys"], ["fable", "other"])
        self.assertNotIn("release 905", self.calls.read_text())
        env = self.env(run_dir.name, NO_KEY="1")
        new = subprocess.run([sys.executable, str(KB), "new", "kumitate", "research", "鍵なしの一時停止", "--body", "-", "--id", "905"],
                             input=TICKET, text=True, capture_output=True, env=env)
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)
        subprocess.run([sys.executable, str(KB), "set", "905", "--run", run_dir.name], env=env, capture_output=True)
        r = subprocess.run([sys.executable, str(KB), "sync", "905"], text=True, capture_output=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("未着手", r.stdout)
        show = subprocess.run([sys.executable, str(KB), "show", "905"], text=True, capture_output=True, env=env).stdout
        self.assertIn("鍵が無いので一時停止", show); self.assertIn("Fable", show); self.assertIn("自動で再開", show)
        # 鍵が無い間は resumable に「登録待ち」で載り、鍵プールに両用途の鍵が入ると ready になる
        keys = self.ws / "keys.json"; env["SANDBOX_KEYS"] = str(keys)
        r = subprocess.run([sys.executable, str(KB), "resumable", "--json"], text=True, capture_output=True, env=env)
        pl = json.loads(r.stdout)[0]
        self.assertEqual((pl["id"], pl["paused"], pl["ready"], pl["step"]), (905, "nokey", False, None))
        keys.write_text(json.dumps({"keys": [{"name": "a", "token": "x", "allow": {"fable": True, "other": False}, "enabled": True}]}))
        r = subprocess.run([sys.executable, str(KB), "resumable", "--json"], text=True, capture_output=True, env=env)
        self.assertFalse(json.loads(r.stdout)[0]["ready"])                    # other が無い
        keys.write_text(json.dumps({"keys": [{"name": "a", "token": "x", "allow": {"fable": True, "other": True}, "enabled": True}]}))
        r = subprocess.run([sys.executable, str(KB), "resumable", "--json"], text=True, capture_output=True, env=env)
        self.assertTrue(json.loads(r.stdout)[0]["ready"])
        r = subprocess.run([sys.executable, str(KB), "resumable"], text=True, capture_output=True, env=env)
        self.assertIn("kb run 905", r.stdout); self.assertNotIn("--from", r.stdout)   # 続きではなく初めから

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
