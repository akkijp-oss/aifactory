"""agent step が Claude の鍵の利用枠（5 時間 / 7 日）や鍵の失効で止まったときの保全と、続きから回すための記録（チケット 380 / ADR-0043）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない（test_agent_timeout.py と同じ流儀。偽装するのは VM と claude だけで、step の遷移・git・agent_command は実物）。

- 利用枠で拒否された implement（rate_limit_event: rejected → rc=1）: 追跡済みの未コミット変更が `wip: usage limit` として wip ブランチに乗る
- `state.json` に `failure: "quota"` / `quota_type` / `retry_after`（resetsAt をオフセット付き ISO に）/ `quota_hits` が残り、
  `resume_step` は戻し先（on_fail.goto）ではなく **その step 自身**。gates → implement の戻し（loops）は消費しない
- 鍵が使えない（stderr に "Invalid API key · Please run /login"、rc=1）: `failure: "key"`、wip は `wip: token unusable`
- agent が読んだコード（tool_result）に "Invalid API key" と書いてあるだけでは鍵の失効と見なさない（普通の失敗のまま）
- classify_agent_stop: 旧形式 "usage limit reached|<epoch>" の epoch を retry_after に、"billing_error … resets" は quota、"Credit balance is too low" は key
- `--from` で続きを回す run は前回の quota_hits を引き継ぎ、最初の依頼文に「wip の続きから」と入る
"""
import datetime
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_loader("agent_quota_run", importlib.machinery.SourceFileLoader("agent_quota_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
run.paths = types.SimpleNamespace(project_dir=run.paths.project_dir, PROJECT_DIRS=run.paths.PROJECT_DIRS, RUNS=None)

TICKET = "# バグ: 利用枠切れで止まったときの保全\n\n利用枠で拒否される implement を再現する。\n"
RESETS_AT = 1900000000   # 2030 年。未来であることだけが要る

# 偽の claude。README.md を直してから「利用枠の上限で拒否された」と言って落ちる（claude CLI の stream-json と同じ形）
CLAUDE_QUOTA = """#!/bin/bash
echo '{"type":"system","subtype":"init","model":"fake-claude","tools":[],"cwd":"'"$PWD"'"}'
printf 'kumitate\\n途中まで書いた実装\\n' > README.md
: > junk.db
echo '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":%d,"rateLimitType":"five_hour","utilization":1.0}}'
echo '{"type":"result","subtype":"error_during_execution","is_error":true,"result":"You'"'"'ve hit your limit · resets 3pm (Asia/Tokyo)","num_turns":3,"duration_ms":1200}'
exit 1
""" % RESETS_AT

CLAUDE_KEY = """#!/bin/bash
echo '{"type":"system","subtype":"init","model":"fake-claude","tools":[],"cwd":"'"$PWD"'"}'
printf 'kumitate\\n鍵が切れる前に書いた分\\n' > README.md
echo 'Invalid API key · Please run /login' >&2
exit 1
"""

# 普通の失敗。tool_result の中に「Invalid API key」という文字列があるが、それは agent が読んだコードの中身
CLAUDE_READS_CODE = """#!/bin/bash
echo '{"type":"system","subtype":"init","model":"fake-claude","tools":[],"cwd":"'"$PWD"'"}'
echo '{"type":"user","message":{"content":[{"type":"tool_result","content":"if (!key) throw new Error(\\"Invalid API key\\")","is_error":false}]}}'
echo '{"type":"result","subtype":"error_max_turns","is_error":true,"result":"","num_turns":50,"duration_ms":1200}'
exit 1
"""


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if p.returncode: raise AssertionError(f"git {' '.join(args)} @ {cwd}: {p.stdout}{p.stderr}")
    return p.stdout


def git_out(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True).stdout


class ClassifyTest(unittest.TestCase):
    def test_rejected_rate_limit_event_is_quota_with_the_reset_time(self):
        kind, info = run.classify_agent_stop({"status": "rejected", "resetsAt": RESETS_AT, "rateLimitType": "seven_day"}, None, "")
        self.assertEqual(kind, "quota")
        self.assertEqual(info["quota_type"], "seven_day")
        self.assertEqual(datetime.datetime.fromisoformat(info["retry_after"]).timestamp(), RESETS_AT)
        self.assertIsNotNone(datetime.datetime.fromisoformat(info["retry_after"]).tzinfo)   # オフセット付き（ADR-0026）

    def test_old_style_message_carries_the_epoch(self):
        kind, info = run.classify_agent_stop(None, {"is_error": True, "result": f"Claude AI usage limit reached|{RESETS_AT}"}, "")
        self.assertEqual(kind, "quota")
        self.assertEqual(datetime.datetime.fromisoformat(info["retry_after"]).timestamp(), RESETS_AT)

    def test_daily_spend_limit_is_quota_and_credit_is_key(self):
        kind, _ = run.classify_agent_stop(None, None, 'API Error: 429 {"type":"billing_error","message":"spend limit reached (daily; resets 2026-08-08 00:00 UTC)"}')
        self.assertEqual(kind, "quota")
        kind, info = run.classify_agent_stop(None, None, "API Error: 400 Credit balance is too low")
        self.assertEqual(kind, "key")
        self.assertIsNone(info["retry_after"])

    def test_hit_your_limit_without_an_event_is_quota_with_unknown_reset(self):
        kind, info = run.classify_agent_stop(None, {"is_error": True, "result": "You've hit your limit · resets 3pm (Asia/Tokyo)"}, "")
        self.assertEqual(kind, "quota")
        self.assertEqual(info["quota_type"], "unknown")
        self.assertIsNone(info["retry_after"])

    def test_session_limit_wording_is_quota_too(self):
        """実機 2026-09-09（kumitate 300 / 327）の文言。result は subtype success のまま is_error だけ true だった"""
        kind, info = run.classify_agent_stop(None, {"subtype": "success", "is_error": True, "result": "You've hit your session limit · resets 2:50am (Asia/Tokyo)"}, "")
        self.assertEqual(kind, "quota")

    def test_a_plain_failure_is_neither(self):
        kind, _ = run.classify_agent_stop(None, {"is_error": True, "subtype": "error_max_turns", "result": ""}, "")
        self.assertIsNone(kind)


class AgentQuotaTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        run.paths.RUNS = self.ws / "runs"
        self.ticket = self.ws / "ticket.md"; self.ticket.write_text(TICKET, encoding="utf-8")
        self.origin = self.ws / "origin.git"
        git(self.ws, "init", "-q", "--bare", "-b", "develop", str(self.origin))
        seed = self.ws / "seed"; seed.mkdir()
        git(seed, "init", "-q", "-b", "develop")
        self.identity(seed)
        (seed / "README.md").write_text("kumitate\n", encoding="utf-8")
        (seed / ".gitignore").write_text("", encoding="utf-8")
        git(seed, "add", "-A"); git(seed, "commit", "-q", "-m", "初期")
        git(seed, "remote", "add", "origin", str(self.origin))
        git(seed, "push", "-q", "-u", "origin", "develop")
        self.app = self.ws / "app"
        git(self.ws, "clone", "-q", str(self.origin), str(self.app))
        self.identity(self.app)
        os.environ.pop("AIFACTORY_FROM_RUN", None)

    def identity(self, d):
        git(d, "config", "user.name", "aifactory test")
        git(d, "config", "user.email", "test@example.invalid")

    def fake_claude(self, body):
        bin_dir = self.ws / "bin"; bin_dir.mkdir(exist_ok=True)
        p = bin_dir / "claude"; p.write_text(body, encoding="utf-8"); p.chmod(0o755)
        return str(bin_dir)

    def build(self, task, claude_body, **kw):
        r = run.Run("kumitate", str(task), "bug", str(self.ticket), **kw)
        self.done = []
        app = str(self.app)
        work = self.ws / "work"; work.mkdir(exist_ok=True)
        r.work = str(work)
        env = {"SANDBOX_APP_DIR": app, "PATH": self.fake_claude(claude_body) + os.pathsep + os.environ["PATH"]}

        def sb(cmd, input_text=None, check=True):
            p = subprocess.run(["bash", "-c", cmd], text=True, capture_output=True, input=input_text, env={**os.environ, **env})
            if check and p.returncode: raise RuntimeError(f"command failed ({p.returncode}): {cmd}\n{p.stderr[-500:]}")
            return p.stdout

        def run_remote(cmd, log_path, render=None, timeout=3600):
            return run.stream(["bash", "-c", cmd], log_path, render=render, env=env)

        real_agent = r.run_agent

        def run_agent(step, retry_note=""):
            self.done.append(step["id"]); self.notes[step["id"]] = retry_note
            if step["id"] == "implement": return real_agent(step, retry_note)
            return True, ""

        def run_code(step):
            self.done.append(step["id"]); return True, ""

        def preserve():
            wip = f"sandbox/{r.task}-{r.wf_name}-wip"
            git(self.app, "push", "-q", "--force", "origin", f"HEAD:refs/heads/{wip}")
            return wip

        self.notes = {}
        r.sb = sb; r.run_remote = run_remote
        r.take = lambda: git(self.app, "checkout", "-q", "-B", r.branch, f"origin/{r.from_branch or 'develop'}")
        r.release = lambda: None; r.refresh_token = lambda: None
        r.scp_to = lambda local, remote: shutil.copy(str(local), str(remote))
        r.preserve = preserve; r.run_agent = run_agent; r.run_code = run_code
        return r

    def test_a_quota_stop_keeps_the_work_and_records_how_to_continue(self):
        r = self.build(921, CLAUDE_QUOTA)
        r.main()
        self.assertEqual(self.done, ["plan", "implement"])                     # gates 以降は呼ばれない
        self.assertEqual(r.state["result"], "human")
        self.assertEqual([(h["step"], h["ok"]) for h in r.state["history"]], [("plan", True), ("implement", False)])
        # 保全: 追跡済みの変更だけが wip コミットに入り、wip ブランチに乗る
        self.assertEqual(git_out(self.app, "log", "-1", "--format=%s").strip(), "wip: usage limit")
        stat = git_out(self.app, "show", "--stat", "--format=", "HEAD")
        self.assertIn("README.md", stat); self.assertNotIn("junk.db", stat)
        self.assertEqual(git_out(self.origin, "log", "-1", "--format=%s", "sandbox/921-bug-wip").strip(), "wip: usage limit")
        # 記録: 利用枠（quota）・種類・解除時刻・回数。やり直しは implement 自身から
        self.assertEqual(r.state["failure"], "quota")
        self.assertEqual(r.state["quota_type"], "five_hour")
        self.assertEqual(datetime.datetime.fromisoformat(r.state["retry_after"]).timestamp(), RESETS_AT)
        self.assertEqual(r.state["quota_hits"], 1)
        self.assertEqual(r.state["resume_step"], "implement")
        self.assertEqual(r.state["loops"], {})                                 # 戻しの回数は消費しない
        last = r.state["history"][-1]
        self.assertEqual(last["failure"], "quota"); self.assertEqual(last["quota_type"], "five_hour")
        err = r.state["error"]
        self.assertIn("implement", err); self.assertIn("quota", err); self.assertIn("five_hour", err)
        self.assertEqual(len([l for l in err.splitlines() if l.strip()]), 1)
        log = (r.run_dir / "agent-implement-1.log").read_text(encoding="utf-8")
        self.assertIn("rate limit: rejected (five_hour", log)

    def test_an_unusable_key_is_key_not_quota(self):
        r = self.build(922, CLAUDE_KEY)
        r.main()
        self.assertEqual(r.state["result"], "human")
        self.assertEqual(r.state["failure"], "key")
        self.assertIsNone(r.state["retry_after"])
        self.assertEqual(r.state["quota_hits"], 0)
        self.assertEqual(r.state["resume_step"], "implement")
        self.assertEqual(git_out(self.app, "log", "-1", "--format=%s").strip(), "wip: token unusable")
        self.assertIn("key", r.state["error"]); self.assertIn("Invalid API key", r.state["error"])

    def test_code_the_agent_read_does_not_look_like_a_dead_key(self):
        """tool_result の中の "Invalid API key" は agent が読んだコード。普通の失敗として扱う（implement は on_fail が無いので human）"""
        r = self.build(923, CLAUDE_READS_CODE)
        r.main()
        self.assertEqual(r.state["result"], "human")
        self.assertNotIn("failure", r.state)
        self.assertNotIn("failure", r.state["history"][-1])
        self.assertIn("implement で止まった", r.state["error"])

    def test_continuing_from_a_quota_stop_carries_the_count_and_points_at_the_wip(self):
        """`--from` の続き: 前回の quota_hits を引き継ぎ、依頼文に「wip の続きから」と入る。もう一度止まれば 2 回目"""
        first = self.build(924, CLAUDE_QUOTA); first.main()
        os.environ["AIFACTORY_FROM_RUN"] = first.run_dir.name
        try:
            r = self.build(924, CLAUDE_QUOTA, from_step="", from_branch=None)
        finally:
            os.environ.pop("AIFACTORY_FROM_RUN", None)
        self.assertEqual(r.from_step, "implement")
        self.assertEqual(r.from_branch, "sandbox/924-bug-wip")
        self.assertEqual(r.state["quota_hits"], 1)                            # 引き継ぎ
        r.main()
        self.assertEqual(self.done, ["implement"])                             # plan は回さない
        self.assertIn("wip", self.notes["implement"]); self.assertIn("続きから", self.notes["implement"])
        self.assertIn("quota", self.notes["implement"])
        self.assertEqual(r.state["quota_hits"], 2)
        self.assertEqual(r.state["resume_step"], "implement")
        # 続きの run の作業ツリーは前回の wip から始まっている（前回の wip コミットが base との差分に残る。今回は同じ内容を書くので新しいコミットは無い）
        self.assertEqual(git_out(self.app, "log", "--format=%s", "origin/develop..HEAD").strip().splitlines(), ["wip: usage limit"])


if __name__ == "__main__":
    unittest.main()
