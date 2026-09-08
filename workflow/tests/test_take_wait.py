"""プールに空きが無いときに待つ `--wait`（チケット 242）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。PATH の先頭に偽の `sandbox` を置いて runner を回す（test_take_failure.py と同じ流儀）。
- 偽 sandbox が 2 回「空きなし」を返してから成功する → take は 3 回呼ばれ、待っている間の state.json は `current.step == "wait-vm"`
- 上限を超えたら `result: failed` に加えて `failure: "wait_timeout"` と `waited_s` が残り、kb はチケットを todo に戻す
- `--wait` 無しの空きなしは従来どおり（test_take_failure.py が見ている）
"""
import importlib.util
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
TICKET = "# 調査: 空き待ちの再現\n\n偽の sandbox で take を空きなしにする。\n"


def runner_module():
    """「空きなし」の目印は runner の定数が正本。テストが自前の文字列を持つと、文言を変えたときに気づけない"""
    spec = importlib.util.spec_from_loader("aifactory_run", importlib.machinery.SourceFileLoader("aifactory_run", str(RUNNER)))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


POOL_BUSY = runner_module().POOL_BUSY

# take のたびに呼び出し回数を数え、そのときの state.json を snapshot にコピーする偽 sandbox。
# TAKE_BUSY_N 回目までは「空きなし」で失敗する（空文字なら常に空きなし）
FAKE = r"""#!/usr/bin/env bash
echo "$@" >> "$CALLS"
case "$1" in
  take)
    n=$(( $(cat "$COUNT" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$COUNT"
    cp "$RUNS/$RUN_NAME/state.json" "$SNAPS/$n.json" 2>/dev/null || true
    if [ -z "$TAKE_BUSY_N" ] || [ "$n" -le "$TAKE_BUSY_N" ]; then
      echo "[error] pj=$2 __BUSY__: 定義 2 台・実体 2 台・貸出 2 台（未構築 0 台 / clean 無し 0 台）" >&2; exit 1
    fi
    echo "take: sb-t-$2-01 10.77.1.1"
    ;;
  ssh)
    if [ -n "$SSH_FAILS" ]; then echo "fatal: couldn't find remote ref develop" >&2; exit 128; fi
    ;;
esac
exit 0
""".replace("__BUSY__", POOL_BUSY)


class TakeWaitTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.bin = self.ws / "bin"; self.bin.mkdir()
        fake = self.bin / "sandbox"; fake.write_text(FAKE); fake.chmod(0o755)
        (self.ws / "sandbox-state.json").write_text("{}")
        self.ticket = self.ws / "ticket.md"; self.ticket.write_text(TICKET, encoding="utf-8")
        self.calls = self.ws / "calls.log"
        self.count = self.ws / "take-count.txt"
        self.snaps = self.ws / "snaps"; self.snaps.mkdir()

    def env(self, run_name, **extra):
        return dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                    AIFACTORY_WORKSPACE=str(self.ws), SANDBOX_STATE=str(self.ws / "sandbox-state.json"),
                    CALLS=str(self.calls), COUNT=str(self.count), SNAPS=str(self.snaps),
                    RUNS=str(self.ws / "runs"), RUN_NAME=run_name, **extra)

    def run_runner(self, task, *flags, **extra):
        import datetime
        name = f"{datetime.date.today().isoformat()}-kumitate-{task}"
        p = subprocess.run([sys.executable, str(RUNNER), "kumitate", task, "research", str(self.ticket), *flags],
                           text=True, capture_output=True, env=self.env(name, **extra))
        return p, self.ws / "runs" / name

    def state(self, run_dir):
        f = run_dir / "state.json"
        self.assertTrue(f.exists(), f"state.json が無い: {sorted(p.name for p in run_dir.iterdir())}")
        return json.loads(f.read_text(encoding="utf-8"))

    def snap(self, n):
        return json.loads((self.snaps / f"{n}.json").read_text(encoding="utf-8"))

    def takes(self):
        return [l for l in self.calls.read_text().splitlines() if l.startswith("take ")]

    def test_waits_until_the_pool_has_room_and_then_takes(self):
        """2 回「空きなし」を返してから成功する偽 sandbox。待っている間は wait-vm と記録される"""
        p, run_dir = self.run_runner("911", "--wait=60", TAKE_BUSY_N="2", AIFACTORY_WAIT_POLL_S="0.1", SSH_FAILS="1")
        self.assertEqual(len(self.takes()), 3, p.stdout + p.stderr)          # 空きなし 2 回 → 3 回目で take できた
        self.assertEqual(self.snap(1)["current"]["step"], "take")            # 1 回目は待つ前（238 の初期書き込み）
        for n in (2, 3):
            self.assertEqual(self.snap(n)["current"], {"step": "wait-vm", "kind": "wait", "log": None,
                                                       "since": self.snap(n)["current"]["since"]}, n)
        self.assertEqual(self.snap(2)["current"]["since"], self.snap(3)["current"]["since"])   # 待ち始めの 1 度だけ
        s = self.state(run_dir)
        self.assertEqual(s["result"], "failed")                              # take 後の ssh でわざと止めている
        self.assertNotIn("failure", s)                                       # 空き待ちで終わったのではない
        self.assertIn("couldn't find remote ref", s["error"])
        self.assertIn("release 911", self.calls.read_text())                 # 借りた VM は返す
        self.assertIn("VM 空き待ち", p.stdout)

    def test_wait_timeout_records_the_reason_and_does_not_take(self):
        """上限を超えたら failure: wait_timeout で終わる。VM は借りていないので返さない"""
        p, run_dir = self.run_runner("912", "--wait=1", AIFACTORY_WAIT_POLL_S="0.2", TAKE_BUSY_N="")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        s = self.state(run_dir)
        self.assertEqual((s["result"], s["next"], s["failure"]), ("failed", "human", "wait_timeout"))
        self.assertGreaterEqual(s["waited_s"], 1)
        self.assertIn("上限", s["error"])
        self.assertIn(POOL_BUSY, s["error"])
        self.assertIsNone(s["current"])
        self.assertNotIn("release 912", self.calls.read_text())
        self.assertGreater(len(self.takes()), 1)                             # 1 回で諦めていない

    def test_kb_run_wait_timeout_puts_the_ticket_back_to_todo(self):
        """上限超過は「人間待ち」ではなく未着手に戻す（空きが出れば回るので、直す所が無い。238 の failed→blocked とは別扱い）"""
        env = self.env("unused", AIFACTORY_WAIT_POLL_S="0.2", TAKE_BUSY_N="")
        new = subprocess.run([sys.executable, str(KB), "new", "kumitate", "research", "空き待ちの再現", "--body", "-", "--id", "913"],
                             input=TICKET, text=True, capture_output=True, env=env)
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)
        import datetime
        name = f"{datetime.date.today().isoformat()}-kumitate-913"
        env["RUN_NAME"] = name
        r = subprocess.run([sys.executable, str(KB), "run", "913", "--wait", "0.02"], text=True, capture_output=True, env=env)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        show = subprocess.run([sys.executable, str(KB), "show", "913"], text=True, capture_output=True, env=env)
        head = show.stdout.split("-" * 60)[0].splitlines()
        t = {l.split(" ", 1)[0]: l.split(" ", 1)[1].strip() for l in head if l.strip()}
        self.assertEqual(t["status"], "todo", t)
        self.assertIn("上限", t["note"])
        self.assertEqual(t["run"], name)
        self.assertEqual(self.state(self.ws / "runs" / name)["failure"], "wait_timeout")


if __name__ == "__main__":
    unittest.main()
