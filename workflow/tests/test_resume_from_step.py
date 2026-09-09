"""human で止まった run を、新しい VM で wip ブランチ・前回の work/ つきで指定の step からやり直す（チケット 333）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。一時 dir に bare の origin と clone（= VM の app dir）を作り、runner の `sb` を
「その clone の中で bash を回す」に、`run_remote` を「同じ env でローカルの bash を回す」に差し替える
（`test_agent_timeout.py` と同じ流儀。`sandbox take` だけ module の `sh` を差し替えて通し、`take()` の
checkout・持ち込み・step の遷移・依頼文の組み立ては実物を動かす）。

- `--from` で始めた run は implement から動く（research / design は呼ばれない）
- 作業ブランチは base ではなく origin/<wip> の続きで、前回の work/*.md が新しい VM に載る
- 最初の依頼文に「前回の結果（直すこと）」として前回の review.md が入る（review からやり直すときは入れない）
- state.json に `resumed_from` / `from_step` / `from_branch` が残り、`loops` は空から数え直す。前回の run は消えない
- 前回が「今日の同じ名前の run」なら、退避先（-attemptN）を前回として読む
- human で止まった run は `resume_step`（やり直す step）を残す: review の 2 連続 FAIL → implement、implement 自身の失敗 → implement
- 人が打つ値の検査: 無い step / 妙なブランチ名 / run 名の形でない AIFACTORY_FROM_RUN は始まらない
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

spec = importlib.util.spec_from_loader("resume_from_run", importlib.machinery.SourceFileLoader("resume_from_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
# 置き場だけ一時 dir に向ける（共有の aifactory_paths は書き換えない。同じプロセスで他の test が読んでいる）
run.paths = types.SimpleNamespace(project_dir=run.paths.project_dir, PROJECT_DIRS=run.paths.PROJECT_DIRS, RUNS=None)

TICKET = "# 機能: 止まった run を続きから回す\n\n2 回目のレビューで止まった run をやり直す。\n"
REVIEW_FAIL = "# レビュー: FAIL\n## 指摘\n1. workflow/bin/run:248 の分岐が 1 件足りない\n"
PLAN = "# 計画: 続きから回す\n\n- 分岐を 1 つ足す\n"
RESEARCH = "# 調査: 既存の resume\n\n- take() が resume で丸ごと return する\n"

# 偽の claude。依頼文の 1 行目（`# 依頼: … / step: <id> / role: <役割>）`）から step を見分け、その step の出力だけ書く。
# implement は「実装した」ことにするため追跡済みのファイルも直す（runner が `git` の出力を確かめるので、コミットが要る）
FAKE_CLAUDE = r"""#!/bin/bash
echo '{"type":"system","subtype":"init","model":"fake-claude","tools":[],"cwd":"'"$PWD"'"}'
step=$(printf '%s' "$2" | head -1 | sed -n 's/.*step: \([a-z-]*\) .*/\1/p')
case "$step" in
  research) printf '# 調査: 偽\n' > "$WORK/research.md" ;;
  design)   printf '# 計画: 偽\n' > "$WORK/plan.md" ;;
  review)   printf '# レビュー: %s\n## 要約\nテストの偽レビュー\n' "${REVIEW:-PASS}" > "$WORK/review.md" ;;
  *)
    if [ "$IMPLEMENT" = "fail" ]; then echo "実装が途中で落ちた"; exit 1; fi
    printf '# 実装報告: 直した\n' > "$WORK/report.md"
    printf 'kumitate\n直した\n' > README.md ;;
esac
echo "step=$step の出力を書いた"
exit 0
"""


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if p.returncode: raise AssertionError(f"git {' '.join(args)} @ {cwd}: {p.stdout}{p.stderr}")
    return p.stdout


def git_out(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True).stdout


class ResumeFromStepTest(unittest.TestCase):
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
        self.env_keys = []
        self.addCleanup(self.unset_env)

    def identity(self, d):
        git(d, "config", "user.name", "aifactory test")
        git(d, "config", "user.email", "test@example.invalid")

    def setenv(self, **kw):
        for k, v in kw.items():
            self.env_keys.append(k); os.environ[k] = v

    def unset_env(self):
        for k in self.env_keys: os.environ.pop(k, None)

    # ---------- 前回の run と wip ブランチ
    def prev_run(self, name, task, *, wip=None, resume_step="implement", work=None, error="review で止まった"):
        d = self.ws / "runs" / name; (d / "work").mkdir(parents=True, exist_ok=True)
        hist = [{"step": s, "ok": ok, "next": "x", "at": "2026-09-06T09:00:00+09:00"} for s, ok in
                [("research", True), ("design", True), ("implement", True), ("gates", True),
                 ("review", False), ("implement", True), ("gates", True), ("review", False)]]
        state = {"pj": "kumitate", "task": str(task), "workflow": "feature", "branch": f"sandbox/{task}-feature-x",
                 "base": "develop", "result": "human", "next": "human", "pr_url": "", "wip_branch": wip,
                 "loops": {"review->implement": 1}, "history": hist, "error": error}
        if resume_step: state["resume_step"] = resume_step
        (d / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        for k, v in (work or {"plan.md": PLAN, "research.md": RESEARCH, "review.md": REVIEW_FAIL}).items():
            (d / "work" / k).write_text(v, encoding="utf-8")
        (d / "work" / "pr_url").write_text("", encoding="utf-8")     # 拡張子が無いものは持ち込まない
        return d

    def push_wip(self, branch, subject="wip: 途中までの実装"):
        git(self.app, "checkout", "-q", "-B", "wip-seed", "origin/develop")
        (self.app / "feature.txt").write_text("途中まで\n", encoding="utf-8")
        git(self.app, "add", "feature.txt"); git(self.app, "commit", "-q", "-m", subject)
        git(self.app, "push", "-q", "--force", "origin", f"HEAD:refs/heads/{branch}")
        head = git_out(self.app, "rev-parse", "HEAD").strip()
        git(self.app, "checkout", "-q", "develop")
        return head

    # ---------- 偽の VM（sb / run_remote / sandbox take だけ差し替え、take の中身と git と遷移は実物）
    def build(self, task, wf="feature", **kw):
        r = run.Run("kumitate", str(task), wf, str(self.ticket), **kw)
        self.assertEqual(r.base, "develop")
        self.done = []
        work = self.ws / f"work-{task}"; work.mkdir(exist_ok=True)
        r.work = str(work)
        bin_dir = self.ws / "bin"; bin_dir.mkdir(exist_ok=True)
        claude = bin_dir / "claude"; claude.write_text(FAKE_CLAUDE, encoding="utf-8"); claude.chmod(0o755)
        env = {"SANDBOX_APP_DIR": str(self.app), "WORK": str(work),
               "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}   # REVIEW / IMPLEMENT は os.environ から渡る

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

        def run_code(step):                                   # gates / sync / pr は偽（この test が見るのは agent の経路）
            self.done.append(step["id"]); return True, ""

        def preserve():
            wip = f"sandbox/{r.task}-{r.wf_name}-wip"
            git(self.app, "push", "-q", "--force", "origin", f"HEAD:refs/heads/{wip}")
            return wip

        r.sb, r.run_remote, r.run_agent, r.run_code, r.preserve = sb, run_remote, run_agent, run_code, preserve
        r.release = lambda: None
        r.refresh_token = lambda: None
        r.scp_to = lambda local, remote: shutil.copy(str(local), str(remote))
        self.env = env
        return r

    def setUpFakeTake(self):
        """`sandbox take` だけ通す（take() の checkout と持ち込みは実物を走らせる）"""
        real_sh = run.sh
        def fake_sh(cmd, check=True, capture=True, input_text=None, env=None):
            if isinstance(cmd, list) and cmd[:2] == ["sandbox", "take"]:
                return subprocess.CompletedProcess(cmd, 0, f"task-{cmd[3]} ready\n", "")
            return real_sh(cmd, check=check, capture=capture, input_text=input_text, env=env)
        run.sh = fake_sh
        self.addCleanup(lambda: setattr(run, "sh", real_sh))

    # ---------- 本命: implement からやり直す
    def test_from_implement_skips_research_and_design_and_carries_the_review(self):
        self.setUpFakeTake()
        prev = self.prev_run("2026-09-06-kumitate-940", 940, wip="sandbox/940-feature-wip")
        head = self.push_wip("sandbox/940-feature-wip")
        self.setenv(AIFACTORY_FROM_RUN=prev.name)
        r = self.build(940, from_step="", from_branch=None)
        # 記録から step とブランチが決まる（人は `kb run <id> --from` だけ打てばよい）
        self.assertEqual((r.from_step, r.from_branch), ("implement", "sandbox/940-feature-wip"))
        self.assertEqual(r.state["next"], "implement")
        r.main()
        # research / design は呼ばれない（時間と費用の二重払いをしない）
        self.assertEqual(self.done, ["implement", "gates", "review", "sync", "pr"])
        # 作業ブランチは base ではなく wip の続き（wip のコミットが HEAD の祖先で、その成果物が作業ツリーに居る）
        self.assertEqual(git_out(self.app, "rev-parse", "--abbrev-ref", "HEAD").strip(), r.branch)
        self.assertEqual(subprocess.run(["git", "merge-base", "--is-ancestor", head, "HEAD"], cwd=str(self.app)).returncode, 0)
        self.assertTrue((self.app / "feature.txt").exists())
        # 前回の work/*.md は新しい VM に載る（拡張子の無い pr_url は運ばない）
        carried = sorted(p.name for p in pathlib.Path(r.work).iterdir())
        self.assertIn("plan.md", carried); self.assertIn("research.md", carried); self.assertNotIn("pr_url", carried)
        # 最初の依頼文に前回のレビュー指摘が入り、書き直させない計画（plan.md）も入る
        prompt = (r.run_dir / "prompt-implement-0.md").read_text(encoding="utf-8")
        self.assertIn("## 前回の結果（直すこと）", prompt)
        self.assertIn("workflow/bin/run:248 の分岐が 1 件足りない", prompt)
        self.assertIn(prev.name, prompt)
        self.assertIn("分岐を 1 つ足す", prompt)
        # 記録: どこから続けたかが残り、loops は数え直し、前回の run は消えない
        self.assertEqual(r.state["resumed_from"], prev.name)
        self.assertEqual(r.state["from_step"], "implement")
        self.assertEqual(r.state["from_branch"], "sandbox/940-feature-wip")
        self.assertEqual(r.state["loops"], {})
        self.assertTrue((prev / "state.json").exists())
        self.assertNotEqual(r.run_dir, prev)

    def test_explicit_step_and_branch_win_over_the_record(self):
        """人が指定した step とブランチが記録より優先される（記録が古い run でも打てる）"""
        self.setUpFakeTake()
        prev = self.prev_run("2026-09-06-kumitate-941", 941, wip="sandbox/941-feature-wip")
        head = self.push_wip("sandbox/941-other-wip")
        self.setenv(AIFACTORY_FROM_RUN=prev.name)
        r = self.build(941, from_step="review", from_branch="sandbox/941-other-wip")
        self.assertEqual((r.from_step, r.from_branch), ("review", "sandbox/941-other-wip"))
        # review からやり直すときは、前回の review.md を「直すこと」として渡さない（自分の指摘を書き写させない）
        self.assertEqual(r.first_retry_note(), "")
        r.main()
        self.assertEqual(self.done, ["review", "sync", "pr"])
        self.assertEqual(git_out(self.app, "rev-parse", "HEAD").strip(), head)
        self.assertNotIn("## 前回の結果（直すこと）", (r.run_dir / "prompt-review-0.md").read_text(encoding="utf-8"))

    def test_a_previous_run_with_the_same_name_is_read_from_the_attempt_dir(self):
        """今日の run をやり直すと前回は -attemptN に退避される。そこを前回として読む（記録も work/ も失わない）"""
        self.setUpFakeTake()
        name = run.run_name("kumitate", "942")
        prev = self.prev_run(name, 942, wip="sandbox/942-feature-wip")
        self.push_wip("sandbox/942-feature-wip")
        self.setenv(AIFACTORY_FROM_RUN=name)
        r = self.build(942, from_step="", from_branch=None)
        self.assertEqual(r.state["resumed_from"], f"{name}-attempt1")
        self.assertTrue((self.ws / "runs" / f"{name}-attempt1" / "work" / "review.md").exists())
        self.assertIn("workflow/bin/run:248", r.first_retry_note())

    def test_without_a_previous_run_the_step_and_branch_must_be_given(self):
        """前回の run が無くても、step とブランチを両方渡せば回せる（work/ と review.md は持ち込めない）"""
        self.setUpFakeTake()
        self.push_wip("sandbox/943-feature-wip")
        r = self.build(943, from_step="implement", from_branch="sandbox/943-feature-wip")
        self.assertIsNone(r.from_run)
        self.assertEqual(r.state["resumed_from"], None)
        self.assertEqual(r.first_retry_note(), "")
        r.main()
        self.assertEqual(self.done, ["implement", "gates", "review", "sync", "pr"])

    def test_the_error_of_the_previous_run_is_carried_when_there_is_no_review(self):
        """レビューが無い（gates や timeout で止まった）run では、前回の停止理由 1 行を「直すこと」に添える"""
        prev = self.prev_run("2026-09-06-kumitate-944", 944, wip="sandbox/944-feature-wip",
                             work={"plan.md": PLAN}, error="implement: 時間上限 60 分で中断（timeout）")
        self.setenv(AIFACTORY_FROM_RUN=prev.name)
        r = self.build(944, from_step="", from_branch=None)
        note = r.first_retry_note()
        self.assertIn("時間上限 60 分で中断", note)
        self.assertIn(prev.name, note)

    # ---------- human で止まったときに残す事実
    def test_a_second_review_fail_records_implement_as_the_step_to_resume_from(self):
        """review の 2 回目 FAIL で human に落ちた run は「implement からやり直せ」を残す（kb / console が読む）"""
        self.setUpFakeTake()
        self.setenv(REVIEW="FAIL")
        r = self.build(945)
        r.main()
        self.assertEqual(r.state["result"], "human")
        self.assertEqual(self.done, ["research", "design", "implement", "gates", "review", "implement", "gates", "review"])
        self.assertEqual(r.state["resume_step"], "implement")
        self.assertIn("review", r.state["error"])

    def test_a_step_without_a_fallback_records_itself(self):
        """戻し先が無い step（implement）が落ちたら、やり直すのはその step 自身"""
        self.setUpFakeTake()
        self.setenv(IMPLEMENT="fail")
        r = self.build(946)
        r.main()
        self.assertEqual(r.state["result"], "human")
        self.assertEqual(r.state["resume_step"], "implement")

    def test_a_run_that_ends_with_a_pr_records_no_resume_step(self):
        """普通に終わった run には resume_step を残さない（再開の口を出す条件にしている）"""
        self.setUpFakeTake()
        r = self.build(947)
        r.main()
        self.assertNotIn("resume_step", r.state)

    # ---------- 人が打つ値の検査
    def test_an_unknown_step_or_a_strange_branch_name_does_not_start(self):
        prev = self.prev_run("2026-09-06-kumitate-948", 948, wip="sandbox/948-feature-wip")
        self.setenv(AIFACTORY_FROM_RUN=prev.name)
        with self.assertRaises(SystemExit):                                  # workflow に無い step
            run.Run("kumitate", "948", "feature", str(self.ticket), from_step="deploy")
        for bad in ("sandbox/x; rm -rf /", "../etc/passwd", "-b", "a..b", "x/", "x.lock", "sandbox/x\nfoo"):
            with self.assertRaises(SystemExit, msg=bad):
                run.Run("kumitate", "948", "feature", str(self.ticket), from_step="implement", from_branch=bad)
        for good in ("sandbox/948-feature-wip", "feature/x_1.2-3"):
            self.assertTrue(run.valid_branch(good), good)

    def test_a_rejected_from_leaves_the_previous_run_where_it_was(self):
        """指定が悪くて始まらないとき、前回の run を -attemptN に動かしたままにしない（記録の在り処を変えない）"""
        name = run.run_name("kumitate", "953")
        prev = self.prev_run(name, 953, wip="sandbox/953-feature-wip")
        self.setenv(AIFACTORY_FROM_RUN=name)
        with self.assertRaises(SystemExit):
            run.Run("kumitate", "953", "feature", str(self.ticket), from_step="deploy")
        self.assertTrue((prev / "state.json").exists())
        self.assertFalse((self.ws / "runs" / f"{name}-attempt1").exists())

    def test_the_previous_run_must_look_like_a_run_of_this_ticket(self):
        """AIFACTORY_FROM_RUN は run 名の形しか受け付けない（他チケットの記録や任意のパスを読ませない。--resume と同じガード）"""
        for bad in ("../2026-09-06-kumitate-949", "2026-09-06-kumitate-950", "2026-09-06-other-949", "/etc"):
            self.setenv(AIFACTORY_FROM_RUN=bad)
            with self.assertRaises(ValueError, msg=bad):
                run.Run("kumitate", "949", "feature", str(self.ticket), from_step="implement", from_branch="sandbox/x-wip")

    def test_nothing_to_resume_from_stops_instead_of_guessing(self):
        """step もブランチも決まらないなら止まる（推し測って research から回し直さない）"""
        with self.assertRaises(SystemExit):                                  # 前回の run も --from の step も無い
            run.Run("kumitate", "951", "feature", str(self.ticket), from_step="")
        prev = self.prev_run("2026-09-06-kumitate-952", 952, wip=None, resume_step=None)
        self.setenv(AIFACTORY_FROM_RUN=prev.name)
        with self.assertRaises(SystemExit):                                  # 記録に wip ブランチが無い
            run.Run("kumitate", "952", "feature", str(self.ticket), from_step="implement")
        # resume_step の無い古い記録は history の最後の step に落ちる（それも無ければ上で止まる）
        r = run.Run("kumitate", "952", "feature", str(self.ticket), from_step="", from_branch="sandbox/952-feature-wip")
        self.assertEqual(r.from_step, "review")


if __name__ == "__main__":
    unittest.main()
