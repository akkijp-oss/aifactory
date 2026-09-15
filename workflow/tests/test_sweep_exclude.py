"""掃き寄せ（`sandbox: uncommitted changes by agent`）が依存ファイルを拾わないこと（チケット 572）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。一時 dir に bare の origin と clone（= VM の app dir）を作り、runner の `sb` を
「その clone の中で bash を回す」に差し替える（`test_agent_timeout.py` と同じ流儀。git は本物が動く）。

- step の終わりの掃き寄せは、実装役が書いた追跡ファイルは拾い、`package.json` / `pnpm-lock.yaml` は拾わない
- 除外したファイルは作業ツリーごと HEAD の内容へ戻す（gates / reviewer が測る木と push する木を一致させる）
- 掃き寄せコミットの本文に、拾ったファイルと除外したファイルの一覧が入る（本文ゼロをやめる）
- 拾ったことが state.json の history（`swept`）と stdout に出る（差分を見ないと分からない状態をやめる）
- app_dir がリポジトリ直下でない PJ（kumitate は apps/ の下）でも、掃き寄せの範囲が縮まない（`:/` が効いている）
- 時間上限の救済（`wip: step timeout`）でも同じ除外が効く（救済そのものの挙動は test_agent_timeout.py が固定）
- `sweep_exclude: []` の PJ は今までどおり全部拾う（既定を切れる）
- run の開始時（checkout / prepare の直後）に汚れていたら HEAD へ戻し、`dirty_at_start` に残す
"""
import importlib.machinery
import importlib.util
import io
import contextlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lib"))
import aifactory_sweep as sweep

spec = importlib.util.spec_from_loader("sweep_run", importlib.machinery.SourceFileLoader("sweep_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
run.paths = types.SimpleNamespace(project_dir=run.paths.project_dir, PROJECT_DIRS=run.paths.PROJECT_DIRS, RUNS=None)

TICKET = "# バグ: 掃き寄せが依存の downgrade を載せる\n\n環境が書き換えた lock が PR に載る。\n"

REPORT = """# 実装報告: 再現テストを足した

## 変更したファイルと理由
## 走らせた検証と結果
## 未検証項目
実ブラウザでの目視（この VM に画面が無い）
"""

# 偽の claude。実装役が意図して直したファイル（README.md）と、環境が書き換えただけの依存ファイル
# （package.json / pnpm-lock.yaml）を両方汚し、未追跡の junk.db も作る。#527 / #542 / #574 で実際に起きた形
CLAUDE_DIRTIES_DEPS = """#!/bin/bash
echo '{"type":"system","subtype":"init","model":"fake-claude","tools":[],"cwd":"'"$PWD"'"}'
echo '実装した'
cd "$SANDBOX_APP_DIR"
root=$(git rev-parse --show-toplevel)
printf 'kumitate\\n実装した\\n' > "$root/README.md"
sed -i 's/\\^22.20.2/^20.17.6/' "$root/apps/web/package.json"
printf 'lockfileVersion: 9.0\\n別の pnpm で作り直した\\n' > "$root/pnpm-lock.yaml"
: > "$root/junk.db"
cat > "$RUNWORK/report.md" <<'EOF'
%s
EOF
exit 0
""" % REPORT

CLAUDE_DIRTIES_DEPS_THEN_HANGS = CLAUDE_DIRTIES_DEPS.replace("exit 0", "exec sleep 30")

# 実装役が自分で依存ファイルを `git add` した便（意図が明示されている）
CLAUDE_STAGES_DEPS = CLAUDE_DIRTIES_DEPS.replace(
    "exit 0", 'git -C "$root" add apps/web/package.json\nexit 0')


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if p.returncode: raise AssertionError(f"git {' '.join(args)} @ {cwd}: {p.stdout}{p.stderr}")
    return p.stdout


def git_out(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True).stdout


class SweepExcludeTest(unittest.TestCase):
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
        (seed / "pnpm-lock.yaml").write_text("lockfileVersion: 9.0\nlibc: glibc\n", encoding="utf-8")
        (seed / "package.json").write_text('{"engines": {"node": ">=22"}}\n', encoding="utf-8")
        web = seed / "apps" / "web"; web.mkdir(parents=True)
        (web / "package.json").write_text('{"devDependencies": {"@types/node": "^22.20.2"}}\n', encoding="utf-8")
        (web / "app.ts").write_text("export const a = 1\n", encoding="utf-8")
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

    # ---------- 偽の VM（sb / run_remote だけ差し替え、git と step の遷移は実物）
    def build(self, task, claude_body=CLAUDE_DIRTIES_DEPS, app_dir=None, timeout_min=5):
        r = run.Run("kumitate", str(task), "bug", str(self.ticket))
        self.done = []
        work = self.ws / "work"; work.mkdir(exist_ok=True)
        r.work = str(work)
        env = {"SANDBOX_APP_DIR": str(app_dir or self.app), "RUNWORK": str(work),
               "PATH": self.fake_claude(claude_body) + os.pathsep + os.environ["PATH"],
               "CLAUDE_CODE_OAUTH_TOKEN_FABLE": "fake-token-pool", "CLAUDE_CODE_OAUTH_TOKEN_OPUS": "fake-token-pool",
               "CLAUDE_CODE_OAUTH_TOKEN_SONNET": "fake-token-pool", "CLAUDE_CODE_OAUTH_TOKEN_HAIKU": "fake-token-pool"}

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
            return True, ""

        def run_code(step):
            self.done.append(step["id"]); return True, ""

        r.sb = sb
        r.run_remote = run_remote
        r.take = lambda: git(self.app, "checkout", "-q", "-B", r.branch, "origin/develop")
        r.release = lambda: None
        r.refresh_token = lambda: None
        r.scp_to = lambda local, remote: shutil.copy(str(local), str(remote))
        r.preserve = lambda: ""
        r.run_agent = run_agent
        r.run_code = run_code
        r.steps["implement"]["timeout_min"] = timeout_min
        return r

    def dirty_deps(self):
        """環境が依存ファイルを書き換えた状態（★退行注入）を作業ツリーに作る"""
        (self.app / "apps/web/package.json").write_text('{"devDependencies": {"@types/node": "^20.17.6"}}\n', encoding="utf-8")
        (self.app / "pnpm-lock.yaml").write_text("lockfileVersion: 9.0\n別の pnpm で作り直した\n", encoding="utf-8")

    def sweep_only(self, r, message="sandbox: uncommitted changes by agent"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): r.commit_tracked(message)
        return buf.getvalue()

    # ---------- a: step の終わりの掃き寄せ（本票の芯）
    def test_the_sweep_commits_the_agents_file_and_leaves_dependency_files_alone(self):
        """★退行注入: package.json / pnpm-lock.yaml を書き換えた状態で掃き寄せが走っても、それを拾わない"""
        r = self.build(920)
        r.main()
        self.assertIn("implement", self.done)
        # 掃き寄せコミットは実装役のファイルだけを拾う
        self.assertEqual(git_out(self.app, "log", "-1", "--format=%s").strip(), "sandbox: uncommitted changes by agent")
        stat = git_out(self.app, "show", "--stat", "--format=", "HEAD")
        self.assertIn("README.md", stat)
        self.assertNotIn("package.json", stat)
        self.assertNotIn("pnpm-lock.yaml", stat)
        self.assertNotIn("junk.db", stat)
        # 除外したファイルは HEAD の内容へ戻る（作業ツリーにも残さない。gates / reviewer が測る木と push する木を合わせる）
        self.assertIn("^22.20.2", (self.app / "apps/web/package.json").read_text(encoding="utf-8"))
        self.assertIn("libc: glibc", (self.app / "pnpm-lock.yaml").read_text(encoding="utf-8"))
        self.assertEqual(git_out(self.app, "status", "--porcelain", "--untracked-files=no").strip(), "")
        self.assertIn("junk.db", git_out(self.app, "status", "--porcelain"))      # 未追跡は触らない
        # 本文に一覧が入る（本文ゼロをやめる）
        body = git_out(self.app, "log", "-1", "--format=%b")
        self.assertIn("README.md", body)
        self.assertIn("apps/web/package.json", body)
        self.assertIn("pnpm-lock.yaml", body)
        self.assertIn("572", body)
        # state.json の history に事実が残る（差分を開かずに気づける唯一の記録）
        swept = [h for h in r.state["history"] if h.get("swept")]
        self.assertEqual([h["step"] for h in swept], ["implement"])
        self.assertEqual(swept[0]["swept"]["committed"], ["README.md"])
        self.assertEqual(sorted(swept[0]["swept"]["excluded"]), ["apps/web/package.json", "pnpm-lock.yaml"])

    def test_the_sweep_is_announced_on_stdout(self):
        """拾ったこと自体が run の出力に出る（今は差分を見ないと分からない）"""
        r = self.build(921)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): r.main()
        out = buf.getvalue()
        self.assertIn("[run] 掃き寄せ", out)
        self.assertIn("README.md", out)
        self.assertIn("除外して戻した", out)
        self.assertIn("apps/web/package.json", out)

    def test_a_sweep_that_only_finds_dependency_files_makes_no_commit(self):
        """拾うものが依存ファイルしか無ければ、コミットは作らない（空のコミットを作らず、汚れだけ戻す）"""
        r = self.build(922)
        before = git_out(self.app, "rev-parse", "HEAD").strip()
        self.dirty_deps()
        self.sweep_only(r)
        self.assertEqual(git_out(self.app, "rev-parse", "HEAD").strip(), before)
        self.assertIn("^22.20.2", (self.app / "apps/web/package.json").read_text(encoding="utf-8"))
        self.assertEqual(r.last_sweep["committed"], [])
        self.assertEqual(sorted(r.last_sweep["excluded"]), ["apps/web/package.json", "pnpm-lock.yaml"])

    def test_nothing_dirty_leaves_no_record(self):
        """汚れていない step は掃き寄せの記録を残さない（毎 step に空の項目を足さない）"""
        r = self.build(923)
        self.sweep_only(r)
        self.assertIsNone(r.last_sweep)
        self.assertEqual(git_out(self.app, "log", "-1", "--format=%s").strip(), "初期")

    # ---------- b: app_dir がリポジトリ直下でない PJ（kumitate は apps/ の下）
    def test_the_sweep_still_covers_the_whole_tree_when_the_app_dir_is_a_subdirectory(self):
        """`git add -u` にパスを付けると cwd 相対になる。`:/` が無いと掃き寄せの範囲が黙って縮む"""
        r = self.build(924, app_dir=self.app / "apps")
        (self.app / "README.md").write_text("ルート直下も直した\n", encoding="utf-8")
        (self.app / "apps/web/app.ts").write_text("export const a = 2\n", encoding="utf-8")
        self.dirty_deps()
        self.sweep_only(r)
        stat = git_out(self.app, "show", "--stat", "--format=", "HEAD")
        self.assertIn("README.md", stat)            # cwd の外（リポジトリ直下）も拾う
        self.assertIn("app.ts", stat)
        self.assertNotIn("package.json", stat)      # 除外は cwd の中でも外でも効く
        self.assertNotIn("pnpm-lock.yaml", stat)
        self.assertIn("^22.20.2", (self.app / "apps/web/package.json").read_text(encoding="utf-8"))

    def test_the_root_manifest_is_excluded_too(self):
        """リポジトリ直下の package.json も既定の glob（`**/package.json`）に入る"""
        r = self.build(925)
        (self.app / "package.json").write_text('{"engines": {"node": ">=20"}}\n', encoding="utf-8")
        (self.app / "README.md").write_text("直した\n", encoding="utf-8")
        self.sweep_only(r)
        self.assertNotIn("package.json", git_out(self.app, "show", "--stat", "--format=", "HEAD"))
        self.assertIn(">=22", (self.app / "package.json").read_text(encoding="utf-8"))

    # ---------- c: 実装役が自分で add した依存変更は意図が明示されている
    def test_a_dependency_change_the_agent_staged_itself_is_kept(self):
        """票が要求する「依存の変更は明示的に要求されたときだけ」。`git add` は実装役の明示なので残す"""
        r = self.build(926, claude_body=CLAUDE_STAGES_DEPS)
        r.main()
        stat = git_out(self.app, "show", "--stat", "--format=", "HEAD")
        self.assertIn("apps/web/package.json", stat)
        self.assertIn("README.md", stat)
        self.assertNotIn("pnpm-lock.yaml", stat)            # add していない lock は今までどおり拾わない
        self.assertIn("^20.17.6", (self.app / "apps/web/package.json").read_text(encoding="utf-8"))

    # ---------- d: 時間上限の救済（挙動不変。除外だけが足される）
    def test_the_timeout_rescue_still_saves_the_work_but_not_the_dependency_files(self):
        """救済は残す（未コミットの実装を次の attempt へ渡す役割）。拾わないのは依存ファイルだけ"""
        r = self.build(927, claude_body=CLAUDE_DIRTIES_DEPS_THEN_HANGS, timeout_min=0.02)
        r.main()
        self.assertEqual(git_out(self.app, "log", "-1", "--format=%s").strip(), run.WIP_TIMEOUT_MESSAGE)
        stat = git_out(self.app, "show", "--stat", "--format=", "HEAD")
        self.assertIn("README.md", stat)
        self.assertNotIn("package.json", stat)
        self.assertNotIn("junk.db", stat)
        self.assertEqual(r.state["history"][-1]["failure"], "timeout")
        self.assertEqual(r.state["history"][-1]["swept"]["committed"], ["README.md"])

    # ---------- e: PJ 側で既定を切れる
    def test_a_project_that_declares_an_empty_exclude_list_sweeps_everything(self):
        """`sweep_exclude: []` は「除外なし」の明示。既定に戻さない"""
        r = self.build(928)
        r.project["sweep_exclude"] = []
        (self.app / "README.md").write_text("直した\n", encoding="utf-8")
        self.dirty_deps()
        self.sweep_only(r)
        stat = git_out(self.app, "show", "--stat", "--format=", "HEAD")
        self.assertIn("apps/web/package.json", stat)
        self.assertIn("pnpm-lock.yaml", stat)
        self.assertNotIn("junk.db", stat)                   # 未追跡は相変わらず拾わない

    def test_a_project_can_replace_the_default_exclude_list(self):
        r = self.build(929)
        r.project["sweep_exclude"] = ["**/*.lock"]
        (self.app / "README.md").write_text("直した\n", encoding="utf-8")
        self.dirty_deps()
        self.sweep_only(r)
        self.assertIn("package.json", git_out(self.app, "show", "--stat", "--format=", "HEAD"))

    # ---------- f: run 開始時の清浄化（票の「追加提案」）
    def test_a_dirty_worktree_at_start_is_restored_and_recorded(self):
        """★退行注入: 作業ツリーを汚した状態で run を始めると、agent の前に戻り、記録が残る"""
        r = self.build(930)
        r.state["history"] = []
        self.dirty_deps()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): r.clean_start("checkout")
        self.assertEqual(git_out(self.app, "status", "--porcelain", "--untracked-files=no").strip(), "")
        self.assertIn("^22.20.2", (self.app / "apps/web/package.json").read_text(encoding="utf-8"))
        self.assertIn("開始時の作業ツリーが汚れていた", buf.getvalue())
        self.assertEqual(r.state["dirty_at_start"],
                         [{"stage": "checkout", "restored": ["apps/web/package.json", "pnpm-lock.yaml"]}])

    def test_a_clean_worktree_at_start_records_nothing(self):
        r = self.build(931)
        r.clean_start("checkout")
        self.assertNotIn("dirty_at_start", r.state)

    def test_untracked_files_survive_the_start_cleanup(self):
        """生成物・DB を消さない（未追跡は触らない）"""
        r = self.build(932)
        (self.app / "junk.db").write_text("x", encoding="utf-8")
        r.clean_start("checkout")
        self.assertTrue((self.app / "junk.db").exists())


class SweepLibTest(unittest.TestCase):
    """規則の正本（lib/aifactory_sweep.py）。run と windows.py はこれを呼ぶだけ"""

    def test_default_excludes_are_the_two_files_the_ticket_named(self):
        self.assertEqual(sweep.excludes({}), ["**/package.json", "**/pnpm-lock.yaml"])
        self.assertEqual(sweep.excludes(None), ["**/package.json", "**/pnpm-lock.yaml"])
        self.assertEqual(sweep.excludes({"sweep_exclude": ["a"]}), ["a"])
        self.assertEqual(sweep.excludes({"sweep_exclude": []}), [])

    def test_add_pathspecs_start_at_the_top_of_the_tree(self):
        specs = sweep.add_pathspecs(["**/package.json"])
        self.assertEqual(specs[0], ":/")
        self.assertEqual(specs[1], ":(top,glob,exclude)**/package.json")

    def test_restore_pathspecs_use_real_paths_not_globs(self):
        self.assertEqual(sweep.restore_pathspecs(["apps/web/package.json"]), [":(top)apps/web/package.json"])

    def test_status_paths_reads_renames_and_quoted_names(self):
        out = sweep.status_paths('M  a.txt\nR  old.txt -> new.txt\n M "a b.txt"\n?? skip\n')
        self.assertEqual(out, ["a.txt", "new.txt", "a b.txt", "skip"])

    def test_split_separates_what_was_staged_from_what_was_left(self):
        committed, excluded = sweep.split(["README.md", "package.json"], ["README.md"])
        self.assertEqual((committed, excluded), (["README.md"], ["package.json"]))

    def test_commit_body_lists_both_sides(self):
        body = sweep.commit_body(["README.md"], ["package.json"])
        self.assertIn("- README.md", body)
        self.assertIn("- package.json", body)
        self.assertIn("572", body)

    def test_commit_body_rule_only_names_the_globs(self):
        body = sweep.commit_body_rule_only(["**/package.json"])
        self.assertIn("**/package.json", body)


if __name__ == "__main__":
    unittest.main()
