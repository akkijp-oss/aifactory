"""`--resume`（貸出中の VM で続き）の再開位置を state.json の履歴から決める（チケット 338）。

  python3 -m unittest discover -s workflow/tests -v

`next` は「次の遷移先」で、失敗した run では必ず `human` になる。それをそのまま開始工程にすると工程ループに
1 度も入らず、何もせず VM を返して終わる（Mac の provision 失敗後の resume で実測）。ここで固定するのは:

- 純関数 `resume_start(state, steps)` の規則（history 空なら先頭工程、あれば最後に走った工程、続きが無いなら止まる）
- 準備で落ちた run（history=[] / next=human）を `--resume` すると workflow の先頭工程から走る
- gates 超過で human になった run を `--resume` すると gates から続く（implement をやり直さない）
- 続きが無い run（PR まで出ている）は VM に触る前に止まる（黙って返却しない）

VM も claude も使わない（`test_resume_from_step.py` と同じ流儀。偽の VM は一時 dir の clone、agent は偽の claude）。
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

spec = importlib.util.spec_from_loader("resume_start_run", importlib.machinery.SourceFileLoader("resume_start_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
run.paths = types.SimpleNamespace(project_dir=run.paths.project_dir, PROJECT_DIRS=run.paths.PROJECT_DIRS, RUNS=None)

TICKET = "# 調査: resume の再開位置\n\n準備で落ちた run を続きから回す。\n"

# 偽の claude。依頼文の 1 行目から step を見分け、その step の出力だけ書く
FAKE_CLAUDE = r"""#!/bin/bash
echo '{"type":"system","subtype":"init","model":"fake-claude","tools":[],"cwd":"'"$PWD"'"}'
step=$(printf '%s' "$2" | head -1 | sed -n 's/.*step: \([a-z-]*\) .*/\1/p')
case "$step" in
  research) printf '# 調査: 偽\n' > "$WORK/research.md" ;;
  judge)    printf '# 要約: 偽\n' > "$WORK/summary.md" ;;
  *)        printf '# 実装報告: 直した\n' > "$WORK/report.md"
            printf 'kumitate\n直した\n' > README.md ;;
esac
echo "step=$step の出力を書いた"
exit 0
"""


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if p.returncode: raise AssertionError(f"git {' '.join(args)} @ {cwd}: {p.stdout}{p.stderr}")
    return p.stdout


class ResumeStartRuleTest(unittest.TestCase):
    """純関数の規則（state.json だけを見る。VM も PJ も要らない）"""
    STEPS = ["implement", "gates", "sync", "pr", "automerge"]

    def start(self, **state):
        return run.resume_start(state, self.STEPS)

    def test_empty_history_restarts_from_the_first_step(self):
        """準備（provision / prepare）で落ちた run: 1 工程も終えていないので先頭から"""
        self.assertEqual(self.start(next="human", history=[], result="failed", failure="prepare"), "implement")

    def test_history_restarts_from_the_step_that_failed_last(self):
        """gates 超過で human になった run は gates から（implement をやり直さない）"""
        hist = [{"step": "implement", "ok": True}, {"step": "gates", "ok": False}]
        self.assertEqual(self.start(next="human", history=hist, resume_step="implement"), "gates")

    def test_a_run_stopped_in_the_middle_keeps_its_next(self):
        """Ctrl-C や runner が落ちた途中（next が遷移先の工程のまま）はそこから"""
        self.assertEqual(self.start(next="gates", history=[{"step": "implement", "ok": True}]), "gates")

    def test_a_finished_run_stops_instead_of_releasing_the_vm(self):
        """末尾が成功で resume_step も無い（PR まで出ている）run に続きは無い → 止まる"""
        hist = [{"step": "pr", "ok": True}, {"step": "automerge", "ok": True}]
        with self.assertRaises(SystemExit):
            self.start(next="human", history=hist, pr_url="https://example.invalid/pr/1")

    def test_next_end_stops(self):
        with self.assertRaises(SystemExit):
            self.start(next="end", history=[{"step": "automerge", "ok": True}])

    def test_a_step_that_is_gone_from_the_workflow_stops(self):
        """古い記録の工程名が今の workflow に無いなら、推し測らずに止まる"""
        with self.assertRaises(SystemExit):
            self.start(next="human", history=[{"step": "deploy", "ok": False}])

    def test_a_failed_last_step_that_has_a_resume_step_still_restarts_from_itself(self):
        """base 確認で戻せなかった回（ok=True でも resume_step あり）は、その工程から回す"""
        hist = [{"step": "implement", "ok": True}, {"step": "gates", "ok": True}]
        self.assertEqual(self.start(next="human", history=hist, resume_step="gates"), "gates")


class ResumeStartRunTest(unittest.TestCase):
    """通し（偽の VM で main() を回す）"""

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
        git(seed, "add", "README.md"); git(seed, "commit", "-q", "-m", "初期")
        git(seed, "remote", "add", "origin", str(self.origin))
        git(seed, "push", "-q", "-u", "origin", "develop")
        self.app = self.ws / "app"
        git(self.ws, "clone", "-q", str(self.origin), str(self.app))
        self.identity(self.app)
        self.took = []
        real_sh = run.sh
        def fake_sh(cmd, check=True, capture=True, input_text=None, env=None):
            if isinstance(cmd, list) and cmd[:2] == ["sandbox", "take"]:
                self.took.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, f"task-{cmd[3]} ready\n", "")
            return real_sh(cmd, check=check, capture=capture, input_text=input_text, env=env)
        run.sh = fake_sh
        self.addCleanup(lambda: setattr(run, "sh", real_sh))

    def identity(self, d):
        git(d, "config", "user.name", "aifactory test")
        git(d, "config", "user.email", "test@example.invalid")

    def prev_state(self, task, wf, **fields):
        """貸出中の VM を残したまま止まった run の記録を置く（--resume はこれを読む）"""
        d = self.ws / "runs" / run.run_name("kumitate", str(task)); d.mkdir(parents=True, exist_ok=True)
        state = {"pj": "kumitate", "task": str(task), "workflow": wf, "branch": f"sandbox/{task}-{wf}-x",
                 "base": "develop", "started": "2026-09-08T09:00:00+09:00", "history": [], "loops": {}, "next": "human"}
        state.update(fields)
        (d / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        return d

    def build(self, task, wf, **kw):
        r = run.Run("kumitate", str(task), wf, str(self.ticket), resume=True, **kw)
        self.done = []
        self.released = []
        work = self.ws / f"work-{task}"; work.mkdir(exist_ok=True)
        r.work = str(work)
        bin_dir = self.ws / "bin"; bin_dir.mkdir(exist_ok=True)
        claude = bin_dir / "claude"; claude.write_text(FAKE_CLAUDE, encoding="utf-8"); claude.chmod(0o755)
        env = {"SANDBOX_APP_DIR": str(self.app), "WORK": str(work),
               "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}

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
            self.done.append(step["id"]); return real_agent(step, retry_note)

        def run_code(step):
            self.done.append(step["id"]); return True, ""

        r.sb, r.run_remote, r.run_agent, r.run_code = sb, run_remote, run_agent, run_code
        r.preserve = lambda: "sandbox/wip"
        r.release = lambda: self.released.append(True)
        r.refresh_token = lambda: None
        r.scp_to = lambda local, remote: shutil.copy(str(local), str(remote))
        r.prepare = lambda: None
        return r

    def test_resume_after_a_failed_prepare_runs_the_workflow_from_the_first_step(self):
        """完了条件 1: history=[] / next=human の run を --resume すると先頭工程から走る（黙って返却しない）"""
        self.prev_state(960, "research", history=[], next="human", result="failed", failure="prepare",
                        error="prepare (provision.sh) が rc=1 で失敗", finished="2026-09-08T09:10:00+09:00", elapsed_s=90)
        r = self.build(960, "research")
        # VM に触る前（__init__）に開始工程が決まり、前回の終わり方は消えている
        self.assertEqual(r.state["next"], "research")
        for gone in ("result", "finished", "error", "failure", "elapsed_s"):
            self.assertNotIn(gone, r.state, gone)
        self.assertEqual(r.main(), 0)
        self.assertEqual(self.done, ["research", "judge"])
        self.assertEqual(r.state["result"], "end")
        self.assertEqual(len(r.state["history"]), 2)
        self.assertEqual(self.took, [])                 # --resume は貸出中の VM をそのまま使う（take し直さない）
        self.assertEqual(len(self.released), 1)

    def test_resume_after_gates_exhausted_continues_from_gates(self):
        """完了条件 2: gates 超過で human になった run は gates から続く（implement をやり直さない）"""
        hist = [{"step": "implement", "ok": True, "next": "gates"}, {"step": "gates", "ok": False, "next": "implement"},
                {"step": "implement", "ok": True, "next": "gates"}, {"step": "gates", "ok": False, "next": "human"}]
        self.prev_state(961, "chore", history=hist, loops={"gates->implement": 2}, next="human",
                        result="human", resume_step="implement", error="gates で止まった", wip_branch="sandbox/961-chore-wip")
        r = self.build(961, "chore")
        self.assertEqual(r.state["next"], "gates")
        r.main()
        self.assertEqual(self.done, ["gates", "sync", "pr"])   # automerge はこの PJ では飛ばす（PR を人が見る）
        self.assertEqual(r.state["history"][:4], hist)          # 前回の履歴は消さない（続きなので）
        self.assertEqual(r.state["loops"], {"gates->implement": 2})

    def test_resume_of_a_finished_run_stops_before_touching_the_vm(self):
        """完了条件 3: 続きが無い run は __init__ で止まる（VM を触らず・返却もしない）"""
        hist = [{"step": "gates", "ok": True, "next": "sync"}, {"step": "sync", "ok": True, "next": "pr"},
                {"step": "pr", "ok": True, "next": "automerge"}, {"step": "automerge", "ok": True, "next": "human"}]
        self.prev_state(962, "chore", history=hist, next="human", result="human",
                        pr_url="https://example.invalid/pr/1", error="automerge: NOMERGE")
        with self.assertRaises(SystemExit):
            run.Run("kumitate", "962", "chore", str(self.ticket), resume=True)
        self.assertEqual(self.took, [])


if __name__ == "__main__":
    unittest.main()
