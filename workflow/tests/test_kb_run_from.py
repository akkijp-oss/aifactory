"""`kb run <id> --from <step> --branch <wip>` が runner に再開の指定を渡し、human のメモに再開コマンドが載る（チケット 333）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。偽 `sandbox` の take を失敗させて短く終える（test_kb_workflow_override.py と同じ流儀）。
- kb が runner を `--from=<step>` `--branch=<名前>` と `AIFACTORY_FROM_RUN=<記録の run>` で呼ぶ（runner の環境変数は偽 sandbox から覗く）
- `--from` と `--resume` は併用できない
- human で止まった run を `kb sync` すると、メモに `kb run <id> --from <step> --branch <wip>` が入る
- 同じチケットの二重再開を断る（実行中の run があるうちは `--from` を拒否。`--force` で通す。チケット 352）
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
KB = REPO / "kanban" / "bin" / "kb"
TICKET = "# 機能: 止まった run を続きから回す\n\n偽の sandbox で take を失敗させる。\n"

# take を失敗させて短く終える。runner がどんな引数と環境変数で呼ばれたかは runner 自身に書き出させる
FAKE = r"""#!/usr/bin/env bash
echo "$@" >> "$CALLS"
case "$1" in
  take) echo "FROM_RUN: ${AIFACTORY_FROM_RUN-未設定}" >> "$CALLS"
        echo "[error] pj=$2 に空きなし" >&2; exit 1 ;;
esac
exit 0
"""


class KbRunFromTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.bin = self.ws / "bin"; self.bin.mkdir()
        fake = self.bin / "sandbox"; fake.write_text(FAKE); fake.chmod(0o755)
        (self.ws / "sandbox-state.json").write_text("{}")
        self.calls = self.ws / "calls.log"
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        AIFACTORY_WORKSPACE=str(self.ws), SANDBOX_STATE=str(self.ws / "sandbox-state.json"),
                        CALLS=str(self.calls))
        self.env.pop("AIFACTORY_FROM_RUN", None)
        self.today = f"{datetime.date.today().isoformat()}-kumitate-905"
        self.prev = "2026-09-06-kumitate-905"
        new = self.kb("new", "kumitate", "feature", "続きから回す", "--body", "-", "--id", "905", input_text=TICKET)
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)

    def kb(self, *args, input_text=None):
        return subprocess.run([sys.executable, str(KB), *args], input=input_text, text=True, capture_output=True, env=self.env)

    def stopped_run(self, name, **kw):
        """human で止まった run の記録（runner が残す形）を置き、チケットの run 欄をそれに向ける"""
        d = self.ws / "runs" / name; d.mkdir(parents=True, exist_ok=True)
        state = {"pj": "kumitate", "task": "905", "workflow": "feature", "branch": "sandbox/905-feature-x", "base": "main",
                 "result": "human", "next": "human", "pr_url": "", "wip_branch": "sandbox/905-feature-wip",
                 "resume_step": "implement", "error": "review で止まった（失敗、または戻せる回数を使い切った）: 指摘 1 件",
                 "started": "2026-09-06T09:00:00+09:00", "finished": "2026-09-06T10:00:00+09:00", "elapsed_s": 3600,
                 "loops": {"review->implement": 1}, "history": [{"step": "review", "ok": False, "next": "human", "at": "2026-09-06T10:00:00+09:00"}]}
        state.update(kw)
        (d / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.kb("set", "905", "--run", name).returncode, 0)
        return name

    def note(self):
        show = self.kb("show", "905")
        return next((l.split(" ", 1)[1].strip() for l in show.stdout.splitlines() if l.startswith("note ")), "")

    def test_kb_passes_the_step_branch_and_previous_run_to_the_runner(self):
        self.stopped_run(self.prev)
        r = self.kb("run", "905", "--from", "implement", "--branch", "sandbox/905-feature-wip")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)                 # take が失敗して終わる（VM は使わない）
        cmd = next(l for l in r.stdout.splitlines() if l.startswith("[kb] "))
        self.assertIn("--from=implement", cmd)
        self.assertIn("--branch=sandbox/905-feature-wip", cmd)
        self.assertNotIn("--resume", cmd)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn(f"FROM_RUN: {self.prev}", calls)                         # 前回の run は環境変数で渡る
        self.assertEqual(self.kb("show", "905").stdout.count(self.today), 1)   # 記録は新しい run に移る（前回は残る）
        self.assertTrue((self.ws / "runs" / self.prev / "state.json").exists())

    def test_from_without_a_step_is_left_to_the_record(self):
        """`--from` だけなら step は runner が記録（resume_step）から決める"""
        self.stopped_run(self.prev)
        r = self.kb("run", "905", "--from")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        cmd = next(l for l in r.stdout.splitlines() if l.startswith("[kb] "))
        self.assertRegex(cmd, r"--from(\s|$)")
        self.assertNotIn("--from=", cmd)

    def test_from_and_resume_cannot_be_used_together(self):
        self.stopped_run(self.prev)
        r = self.kb("run", "905", "--from", "implement", "--resume")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--from", r.stdout + r.stderr)
        self.assertFalse(self.calls.exists(), "runner を呼ぶ前に止まること")

    def test_a_human_run_gets_the_resume_command_in_its_note(self):
        """板を見た人が組み立て直さずに打てるよう、メモの末尾に再開コマンドを置く"""
        self.stopped_run(self.prev)
        self.assertEqual(self.kb("sync", "905").returncode, 0)
        note = self.note()
        self.assertIn("人間へ", note)
        self.assertIn("kb run 905 --from implement --branch sandbox/905-feature-wip", note)

    def test_a_rejected_from_keeps_the_previous_run_in_the_ledger(self):
        """step を打ち間違えて runner が記録を残さず落ちたら、台帳の run は前回のまま（打ち直せる状態を壊さない）"""
        self.stopped_run(self.prev)
        r = self.kb("run", "905", "--from", "implemnt")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.ws / "runs" / self.today / "state.json").exists(), "記録が残っていない前提のテスト")
        show = self.kb("show", "905").stdout
        self.assertIn(self.prev, show)                                         # 前回の run に戻っている
        self.assertNotIn(self.today, show)
        self.assertIn("kb run 905 --from implement --branch sandbox/905-feature-wip", self.note())

    # ---------- 二重再開の抑止（チケット 352 / ADR-0053）
    def running_run(self, name):
        """まだ終わっていない run（state.json に finished が無い）を置き、台帳も実行中にする"""
        self.stopped_run(name)
        st = self.ws / "runs" / name / "state.json"
        s = json.loads(st.read_text(encoding="utf-8"))
        for k in ("finished", "result", "elapsed_s"): s.pop(k, None)
        st.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.kb("set", "905", "--status", "in_progress").returncode, 0)
        return name

    def test_a_second_resume_of_the_same_ticket_is_refused(self):
        """wip ブランチは task + workflow で決まるので、2 本同時に再開すると後勝ちで上書きされる（ADR-0036）"""
        self.running_run(self.prev)
        r = self.kb("run", "905", "--from", "implement")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(self.prev, r.stdout + r.stderr)                          # どの run が実行中かが分かる
        self.assertIn("--force", r.stdout + r.stderr)
        self.assertFalse(self.calls.exists(), "runner を呼ぶ前に止まること")

    def test_force_lets_the_second_resume_through(self):
        self.running_run(self.prev)
        r = self.kb("run", "905", "--from", "implement", "--force")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)                 # take が失敗して終わる（= runner まで届いた）
        self.assertIn("--from=implement", next(l for l in r.stdout.splitlines() if l.startswith("[kb] ")))
        self.assertIn(f"FROM_RUN: {self.prev}", self.calls.read_text(encoding="utf-8"))

    def test_a_finished_run_does_not_block_the_resume(self):
        """台帳が in_progress のまま古い（run は終わっている）だけなら通す。sync 漏れで再開できなくならない"""
        self.stopped_run(self.prev)                                            # finished つき
        self.assertEqual(self.kb("set", "905", "--status", "in_progress").returncode, 0)
        r = self.kb("run", "905", "--from", "implement")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue(self.calls.exists())

    def test_a_ticket_that_is_not_in_progress_does_not_block_the_resume(self):
        """板が todo / review に戻っている（利用枠で止まった run など）なら、記録の finished を問わず通す"""
        self.running_run(self.prev)
        self.assertEqual(self.kb("set", "905", "--status", "todo").returncode, 0)
        r = self.kb("run", "905", "--from", "implement")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue(self.calls.exists())

    def test_a_plain_run_is_not_blocked(self):
        """--from を使わない普通の実行は今までどおり（このガードは再開の経路だけ）"""
        self.running_run(self.prev)
        r = self.kb("run", "905")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue(self.calls.exists())

    def test_a_human_run_without_a_wip_branch_keeps_the_old_note(self):
        """wip も resume_step も無い（古い）記録では、従来どおりのメモにする（打てないコマンドを出さない）"""
        self.stopped_run(self.prev, wip_branch="", resume_step=None)
        self.assertEqual(self.kb("sync", "905").returncode, 0)
        note = self.note()
        self.assertIn("人間へ", note)
        self.assertNotIn("--from", note)


if __name__ == "__main__":
    unittest.main()
