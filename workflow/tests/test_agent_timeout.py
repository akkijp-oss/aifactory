"""agent step が時間上限（`timeout <timeout_min>m claude …`）で切られたときの保全（チケット 329）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。一時 dir に bare の origin と clone（= VM の app dir）を作り、runner の `sb` を
「その clone の中で bash を回す」に、`run_remote` を「同じ env でローカルの bash を回す」に差し替える
（`test_sync_base.py` と同じ流儀で、偽装するのは VM と claude だけ。`agent_command` と `timeout` の実物はそのまま動かす）。

- 上限で切られた implement: rc=124 → 追跡済みの未コミット変更が `wip: step timeout` として残り、wip ブランチに push される
- 未追跡ファイル（生成物・DB）はその wip コミットに入らない
- `state.json` の `error` は「implement: 時間上限 N 分で中断（timeout）」の 1 行で、agent の stdout は `last_output` に分かれる
- `history` の末尾に `failure: "timeout"` と `timeout_min` が残る（console / MCP が「時間上限で中断」と読む目印）
- 切られたが変更が 1 つも無いときは、空のコミットを作らない
"""
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

spec = importlib.util.spec_from_loader("agent_timeout_run", importlib.machinery.SourceFileLoader("agent_timeout_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
# 置き場だけ一時 dir に向ける（共有の aifactory_paths は書き換えない。同じプロセスで他の test が読んでいる）
run.paths = types.SimpleNamespace(project_dir=run.paths.project_dir, PROJECT_DIRS=run.paths.PROJECT_DIRS, RUNS=None)

TICKET = "# バグ: 時間上限で切られたときの保全\n\n上限を超える implement を再現する。\n"

# 偽の claude。追跡済みの README.md を直し、未追跡の junk.db を作ってから上限を超えるまで眠る。
# `exec sleep` にするのは、timeout が TERM を送る相手を sleep 自身にして、stdout のパイプが即座に閉じるようにするため
# （bash のまま眠らせると、孤児になった sleep がパイプの書き込み端を握り続けて runner が 30 秒待たされる）
CLAUDE_WRITES = """#!/bin/bash
echo '{"type":"system","subtype":"init","model":"fake-claude","tools":[],"cwd":"'"$PWD"'"}'
echo '途中まで書いた: README.md に 1 行足した'
printf 'kumitate\\n途中まで書いた実装\\n' > README.md
: > junk.db
exec sleep 30
"""

CLAUDE_WRITES_NOTHING = """#!/bin/bash
echo '調べているうちに上限を超えた'
exec sleep 30
"""


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if p.returncode: raise AssertionError(f"git {' '.join(args)} @ {cwd}: {p.stdout}{p.stderr}")
    return p.stdout


def git_out(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True).stdout


class AgentTimeoutTest(unittest.TestCase):
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

    def identity(self, d):
        git(d, "config", "user.name", "aifactory test")
        git(d, "config", "user.email", "test@example.invalid")

    def fake_claude(self, body):
        bin_dir = self.ws / "bin"; bin_dir.mkdir(exist_ok=True)
        p = bin_dir / "claude"; p.write_text(body, encoding="utf-8"); p.chmod(0o755)
        return str(bin_dir)

    # ---------- 偽の VM（sb / run_remote だけ差し替え、step の遷移と git と timeout は実物）
    def build(self, task, claude_body, timeout_min=0.02):
        r = run.Run("kumitate", str(task), "bug", str(self.ticket))
        self.assertEqual(r.base, "develop")
        self.done = []
        app = str(self.app)
        work = self.ws / "work"; work.mkdir(exist_ok=True)
        r.work = str(work)
        env = {"SANDBOX_APP_DIR": app, "PATH": self.fake_claude(claude_body) + os.pathsep + os.environ["PATH"]}

        def sb(cmd, input_text=None, check=True):
            p = subprocess.run(["bash", "-c", cmd], text=True, capture_output=True, input=input_text,
                               env={**os.environ, **env})
            if check and p.returncode:
                raise RuntimeError(f"command failed ({p.returncode}): {cmd}\n{p.stderr[-500:]}")
            return p.stdout

        def run_remote(cmd, log_path, render=None, timeout=3600):
            return run.stream(["bash", "-c", cmd], log_path, render=render, env=env)

        real_agent = r.run_agent

        def run_agent(step, retry_note=""):
            self.done.append(step["id"])
            if step["id"] == "implement": return real_agent(step, retry_note)
            return True, ""                                   # plan は通す（この test が見るのは implement だけ）

        def run_code(step):                                   # implement が human 行きなので 1 度も呼ばれないこと
            self.done.append(step["id"]); return True, ""

        def preserve():
            wip = f"sandbox/{r.task}-{r.wf_name}-wip"
            git(self.app, "push", "-q", "--force", "origin", f"HEAD:refs/heads/{wip}")
            return wip

        r.sb = sb
        r.run_remote = run_remote
        r.take = lambda: git(self.app, "checkout", "-q", "-B", r.branch, "origin/develop")
        r.release = lambda: None
        r.refresh_token = lambda: None
        r.scp_to = lambda local, remote: shutil.copy(str(local), str(remote))
        r.preserve = preserve
        r.run_agent = run_agent
        r.run_code = run_code
        r.steps["implement"]["timeout_min"] = timeout_min
        return r

    def test_agent_command_puts_the_step_timeout_on_the_timeout_command(self):
        """上限は `timeout <timeout_min>m` で効いている（この test が実運用と同じ経路を通ることの担保）"""
        r = self.build(910, CLAUDE_WRITES)
        cmd = run.Run.agent_command(r, "/home/dev/work/910/prompt.md", "claude-opus-5", 60)
        self.assertIn("timeout 60m claude -p ", cmd)

    def test_a_timed_out_step_keeps_the_unfinished_work_as_a_wip_commit(self):
        """上限で切られても、追跡済みの未コミット変更は `wip: step timeout` として残り、wip ブランチに乗る"""
        r = self.build(911, CLAUDE_WRITES)
        r.main()
        self.assertEqual(self.done, ["plan", "implement"])          # gates / review / sync / pr は呼ばれない
        self.assertEqual(r.state["result"], "human")
        self.assertEqual([(h["step"], h["ok"]) for h in r.state["history"]], [("plan", True), ("implement", False)])
        # 偽 claude は実際に走っている（`timeout` の実物を通り、rc=124 で切られた経路）
        self.assertIn("fake-claude", (r.run_dir / "agent-implement-1.log").read_text(encoding="utf-8"))
        # 保全: 追跡済みの変更だけが wip コミットに入る
        self.assertEqual(git_out(self.app, "log", "-1", "--format=%s").strip(), "wip: step timeout")
        stat = git_out(self.app, "show", "--stat", "--format=", "HEAD")
        self.assertIn("README.md", stat)
        self.assertNotIn("junk.db", stat)
        self.assertIn("junk.db", git_out(self.app, "status", "--porcelain"))   # 未追跡のまま残る
        # 退避ブランチに中身が乗る（コミット 0 のまま消えない）
        self.assertEqual(r.state["wip_branch"], "sandbox/911-bug-wip")
        self.assertEqual(git_out(self.origin, "log", "-1", "--format=%s", "sandbox/911-bug-wip").strip(), "wip: step timeout")
        # 失敗理由: timeout と上限分数が読め、agent の stdout は混ざらない
        err = r.state["error"]
        self.assertIn("timeout", err)
        self.assertIn("implement", err)
        self.assertIn("0.02", err)
        self.assertNotIn("README.md", err)
        self.assertEqual(len([l for l in err.splitlines() if l.strip()]), 1)   # kb の note が 1 行で読める
        self.assertIn("README.md", r.state["last_output"])
        last = r.state["history"][-1]
        self.assertEqual(last["failure"], "timeout")
        self.assertEqual(last["timeout_min"], 0.02)

    def test_a_timed_out_step_with_nothing_to_save_makes_no_commit(self):
        """切られたが変更が 1 つも無ければ、空のコミットは作らない（理由の文は同じ）"""
        r = self.build(912, CLAUDE_WRITES_NOTHING)
        base = git_out(self.app, "rev-parse", "origin/develop").strip()
        r.main()
        self.assertEqual(r.state["result"], "human")
        self.assertEqual(git_out(self.app, "rev-parse", "HEAD").strip(), base)
        self.assertIn("timeout", r.state["error"])
        self.assertIn("0.02", r.state["error"])
        self.assertEqual(r.state["history"][-1]["failure"], "timeout")

    def test_the_prompt_tells_the_agent_the_limit_and_asks_for_wip_commits(self):
        """依頼文に上限分数と「30 分ごとに wip コミット」が入る（切られる前提を agent に伝える）"""
        r = self.build(913, CLAUDE_WRITES, timeout_min=60)
        prompt = run.Run.build_prompt(r, r.steps["implement"])
        self.assertIn("時間上限は 60 分", prompt)
        self.assertIn("wip:", prompt)
        self.assertIn("30 分", prompt)


if __name__ == "__main__":
    unittest.main()
