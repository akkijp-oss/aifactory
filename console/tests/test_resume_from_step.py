"""human で止まった run の「続きから回すコマンド」を console が組む（チケット 333）。

  python3 -m unittest discover -s console/tests -p 'test_resume_from_step.py' -v

既存の `--resume`（貸出中の同じ VM で続ける。test_resume_hint.py）とは別物で、こちらは新しい VM で
wip ブランチの続きから指定の step をやり直す口。

- `resume_command`: 事実（result: human / wip_branch / resume_step）が揃った run だけコマンドを返す。
  文言は console が組み、runner の記録には事実だけを置く（ADR-0025 / ADR-0034）
- `run_summary` と `run_outcome` に `resume` が載る（run 画面と ticket_show の runs[] が同じ値を出す）
- `ticket_run`: `from_step` / `from_branch` を `kb run` に渡し、記録は今日の run 名で始まる（前回の run を上書きしない）
"""
import datetime
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("resume_from_core", ROOT / "console/lib/core.py")
core = importlib.util.module_from_spec(spec); spec.loader.exec_module(core)

STOPPED = {"pj": "kumitate", "task": "998", "workflow": "feature", "branch": "sandbox/998-feature-x", "base": "main",
           "started": "2026-09-06T09:00:00+09:00", "finished": "2026-09-06T10:00:00+09:00", "elapsed_s": 3600,
           "result": "human", "next": "human", "pr_url": "", "wip_branch": "sandbox/998-feature-wip",
           "resume_step": "implement", "error": "review で止まった: 指摘 1 件", "current": None,
           "loops": {"review->implement": 1},
           "history": [{"step": "review", "ok": False, "next": "human", "at": "2026-09-06T10:00:00+09:00"}]}


class ResumeCommandTest(unittest.TestCase):
    def test_a_human_run_with_a_wip_branch_gets_a_command(self):
        self.assertEqual(core.resume_command("998", STOPPED), "kb run 998 --from implement --branch sandbox/998-feature-wip")

    def test_runs_that_cannot_be_resumed_get_nothing(self):
        """打てないコマンドは出さない（PR まで進んだ run、wip も止まった step も無い run）"""
        for missing in ({"result": "end"}, {"wip_branch": ""}, {"resume_step": None}, {"result": "failed"}):
            self.assertIsNone(core.resume_command("998", {**STOPPED, **missing}), missing)
        self.assertIsNone(core.resume_command(None, STOPPED))

    def test_the_summary_and_the_outcome_carry_the_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp) / "2026-09-06-kumitate-998"; d.mkdir()
            (d / "state.json").write_text(json.dumps(STOPPED, ensure_ascii=False), encoding="utf-8")
            s = core.run_summary(d)
            self.assertEqual(s["resume"], "kb run 998 --from implement --branch sandbox/998-feature-wip")
            self.assertEqual(s["resume_step"], "implement")
            o = core.run_outcome(d, s, STOPPED, {"steps": []}, [])
            self.assertEqual(o["resume"], s["resume"])
            self.assertEqual(o["reason"], "step_failed")     # 理由の導き方は変えていない

    def test_a_run_that_ended_with_a_pr_has_no_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp) / "2026-09-06-kumitate-997"; d.mkdir()
            state = {**STOPPED, "result": "end", "pr_url": "https://example.invalid/pull/1", "wip_branch": ""}
            (d / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            s = core.run_summary(d)
            self.assertIsNone(s["resume"])
            self.assertIsNone(core.run_outcome(d, s, state, {"steps": []}, [])["resume"])


class TicketRunFromTest(unittest.TestCase):
    def call(self, body):
        ticket = {"id": 998, "pj": "kumitate", "run": "2026-09-06-kumitate-998"}
        with patch.object(core, "rows", return_value=[ticket]), patch.object(core.JobStore, "start", return_value={}) as start:
            core.ticket_run(998, body)
        return start.call_args

    def test_from_step_and_branch_reach_kb_run(self):
        a = self.call({"from_step": "implement", "from_branch": "sandbox/998-feature-wip"})
        cmd = a.args[1]
        self.assertIn("--from=implement", cmd)
        self.assertIn("--branch=sandbox/998-feature-wip", cmd)
        self.assertNotIn("--resume", cmd)
        self.assertIn("--from implement", a.args[2])                       # ジョブ一覧に何をしたかが出る
        # 記録は今日の run から始まる（前回の run を上書きしない）
        self.assertEqual(a.kwargs["run_hint"], f"{datetime.date.today().isoformat()}-kumitate-998")

    def test_from_step_can_be_left_to_the_record(self):
        cmd = self.call({"from_step": ""}).args[1]
        self.assertIn("--from", cmd)
        self.assertNotIn("--from=", " ".join(cmd))

    def test_a_plain_run_is_unchanged(self):
        a = self.call({})
        self.assertNotIn("--from", " ".join(a.args[1]))
        self.assertEqual(a.args[2], "kb run 998")


if __name__ == "__main__":
    unittest.main()
