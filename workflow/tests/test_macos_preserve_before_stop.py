"""チケット 477: 停止前の保全コマンド（`preserve_command()`）が、押す価値のあるときだけ wip を進める。

  python3 -m unittest discover -s workflow/tests -v

VM も worker も使わない。一時 dir に bare の origin と clone（= ゲストの app dir）を作り、
制御系が payload に載せる保全コマンドを **実物の bash と git で** 走らせる（test_macos_preserve.py と同じ流儀）。

worker はこのコマンドを取り消し・timeout・watchdog のどの停止でも走らせるので、まだ 1 つも実装が
乗っていない作業ブランチでも走る。無条件の force push だと、その回が **前の run が保全した wip を
base まで巻き戻して消す**（ADR-0053 が「取り返すには reflog が要る」と書いている事故）。

- 実装が乗っている・wip が無い: wip が作られ、作業ブランチの先頭と一致する
- 実装が乗っていない・wip がある: wip は動かない（巻き戻さない）。終了コードは 0（停止を妨げない）
- 実装が乗っていない・wip も無い: 何も作らない
- wip から再開して進んだ: wip は進む（早送りなので巻き戻しではない）
- wip と別系統に分岐している: wip は動かない
"""
import importlib.machinery
import importlib.util
import os
import pathlib
import subprocess
import tempfile
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_loader("macos_stop_preserve_run", importlib.machinery.SourceFileLoader("macos_stop_preserve_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
run.paths = types.SimpleNamespace(project_dir=run.paths.project_dir, PROJECT_DIRS=run.paths.PROJECT_DIRS, RUNS=None)

mspec = importlib.util.spec_from_file_location("macos_stop_preserve_backend", REPO / "workflow/lib/macos.py")
macos = importlib.util.module_from_spec(mspec)
mspec.loader.exec_module(macos)
MacRun = macos.backend(run.Run)

TICKET = "# バグ: 停止で実装が消える\n\n取り消し由来の停止を再現する。\n"


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if p.returncode: raise AssertionError(f"git {' '.join(args)} @ {cwd}: {p.stdout}{p.stderr}")
    return p.stdout


def git_out(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True).stdout.strip()


class PreserveBeforeStopTest(unittest.TestCase):
    TASK = 477
    WIP = "sandbox/477-bug-wip"

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        run.paths.RUNS = self.ws / "runs"
        self.ticket = self.ws / "ticket.md"; self.ticket.write_text(TICKET, encoding="utf-8")
        self.origin = self.ws / "origin.git"
        git(self.ws, "init", "-q", "--bare", "-b", "develop", str(self.origin))
        seed = self.ws / "seed"; seed.mkdir()
        git(seed, "init", "-q", "-b", "develop"); self.identity(seed)
        (seed / "README.md").write_text("kumitate\n", encoding="utf-8")
        git(seed, "add", "README.md"); git(seed, "commit", "-q", "-m", "初期")
        git(seed, "remote", "add", "origin", str(self.origin))
        git(seed, "push", "-q", "-u", "origin", "develop")
        self.app = self.ws / "app"
        git(self.ws, "clone", "-q", str(self.origin), str(self.app))
        self.identity(self.app)
        self.r = self.build()
        # setup_project と同じ形で作業ブランチを作る（origin/<base> からの、コミットゼロのブランチ）
        git(self.app, "checkout", "-q", "-b", self.r.branch, "origin/develop")

    def identity(self, d):
        git(d, "config", "user.name", "aifactory test")
        git(d, "config", "user.email", "test@example.invalid")

    def build(self):
        r = object.__new__(MacRun)
        run.Run.__init__(r, "kumitate", str(self.TASK), "bug", str(self.ticket))
        self.assertEqual(r.base, "develop")
        work = self.ws / "work"; work.mkdir(exist_ok=True)
        r.work = str(work)
        r.env_file = str(work / "runtime.env")            # ゲストの runtime.env は無くてよい（source は条件つき）
        r.project = {**r.project, "app_dir": str(self.app)}
        return r

    def preserve(self):
        """worker が停止の直前に走らせるのと同じ形（素の bash でコマンド 1 本）"""
        return subprocess.run(["bash", "-c", self.r.preserve_command()], text=True,
                              capture_output=True, env={k: v for k, v in os.environ.items() if k != "SANDBOX_APP_DIR"})

    def work_commit(self, name="fix.py"):
        (self.app / name).write_text("# 実装した直し\n", encoding="utf-8")
        git(self.app, "add", name); git(self.app, "commit", "-q", "-m", "実装した")
        return git_out(self.app, "rev-parse", "HEAD")

    def seed_wip(self):
        """前の run が保全した wip を origin に置く（作業ブランチとは別の clone から押す）"""
        other = self.ws / "other"
        git(self.ws, "clone", "-q", str(self.origin), str(other)); self.identity(other)
        git(other, "checkout", "-q", "-b", "old", "origin/develop")
        (other / "earlier.py").write_text("# 前の run の実装\n", encoding="utf-8")
        git(other, "add", "earlier.py"); git(other, "commit", "-q", "-m", "前の run の実装")
        git(other, "push", "-q", "origin", "refs/heads/old:refs/heads/" + self.WIP)
        return git_out(other, "rev-parse", "HEAD")

    # ---------- 押す回

    def test_work_on_the_branch_is_pushed_to_a_fresh_wip_branch(self):
        head = self.work_commit()
        p = self.preserve()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("preserved", p.stdout)
        self.assertEqual(git_out(self.origin, "rev-parse", self.WIP), head)

    def test_a_run_resumed_from_the_wip_branch_fast_forwards_it(self):
        old = self.seed_wip()
        git(self.app, "fetch", "-q", "origin", self.WIP)
        git(self.app, "checkout", "-q", "-B", self.r.branch, "FETCH_HEAD")   # bin/run の --from wip と同じ
        head = self.work_commit("more.py")
        self.assertNotEqual(head, old)
        p = self.preserve()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("preserved", p.stdout)
        self.assertEqual(git_out(self.origin, "rev-parse", self.WIP), head)

    # ---------- 押さない回（巻き戻し・空 push）

    def test_an_empty_work_branch_does_not_rewind_an_existing_wip_branch(self):
        """完了条件: 実装が乗る前に取り消されても、前の run の wip を base で上書きしない"""
        old = self.seed_wip()
        p = self.preserve()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)        # 停止を妨げない
        self.assertNotIn("preserved", p.stdout)
        self.assertIn("skipped", p.stdout)
        self.assertEqual(git_out(self.origin, "rev-parse", self.WIP), old)

    def test_an_empty_work_branch_does_not_create_a_wip_branch(self):
        p = self.preserve()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("skipped", p.stdout)
        self.assertEqual(git_out(self.origin, "rev-parse", "--verify", self.WIP), "")

    def test_a_branch_that_diverged_from_the_wip_branch_does_not_rewind_it(self):
        """wip を含まない系統（PR 再開など）は押さない。含んでいる方を残す"""
        old = self.seed_wip()
        self.work_commit("other.py")
        p = self.preserve()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("skipped", p.stdout)
        self.assertEqual(git_out(self.origin, "rev-parse", self.WIP), old)

    # ---------- 保全できない回

    def test_a_broken_remote_fails_without_breaking_the_command(self):
        """push できないとき（ネットワーク・認証）は非ゼロで終わるだけ。worker はログに理由を残して停止へ進む"""
        self.work_commit()
        git(self.app, "remote", "set-url", "--push", "origin", str(self.ws / "missing.git"))
        p = self.preserve()
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("preserved", p.stdout)


if __name__ == "__main__":
    unittest.main()
