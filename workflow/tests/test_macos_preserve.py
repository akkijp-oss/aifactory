"""pull backend（macOS）で human に落ちた run の保全（チケット 282）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。一時 dir に bare の origin と clone（= ゲストの app dir）を作り、`sb` を
「その clone の中で bash を回す」に差し替える（`test_agent_timeout.py` と同じ流儀）。preserve と
main() の遷移、git は実物を動かす。

- gates を使い切って human: 作業ブランチの HEAD が `sandbox/<id>-<kind>-wip` に push され、
  `state.json` の `wip_branch` に名前が残る（Proxmox backend と同じ）
- push できない: `wip_branch` は空、`wip.patch` に差分が残り、release（成果物回収）は続く
- worker が落ちて sb が例外: preserve は例外を外に出さず、release は呼ばれる
- step の途中で制御系 sqlite が locked（チケット 446）: human に落ちる前に wip を push する。lease と
  ゲストは人の検査用に残したいので release は呼ばない
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import sqlite3
import subprocess
import tempfile
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_loader("macos_preserve_run", importlib.machinery.SourceFileLoader("macos_preserve_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
# 置き場だけ一時 dir に向ける（共有の aifactory_paths は書き換えない。同じプロセスで他の test が読んでいる）
run.paths = types.SimpleNamespace(project_dir=run.paths.project_dir, PROJECT_DIRS=run.paths.PROJECT_DIRS, RUNS=None)

mspec = importlib.util.spec_from_file_location("macos_preserve_backend", REPO / "workflow/lib/macos.py")
macos = importlib.util.module_from_spec(mspec)
mspec.loader.exec_module(macos)
MacRun = macos.backend(run.Run)

TICKET = "# バグ: human 落ちで実装が消える\n\ngates を使い切った run を再現する。\n"


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if p.returncode: raise AssertionError(f"git {' '.join(args)} @ {cwd}: {p.stdout}{p.stderr}")
    return p.stdout


def git_out(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True).stdout


class MacPreserveTest(unittest.TestCase):
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

    def identity(self, d):
        git(d, "config", "user.name", "aifactory test")
        git(d, "config", "user.email", "test@example.invalid")

    # ---------- 偽の Mac worker（sb と step の入出力だけ差し替え、遷移と preserve と git は実物）
    def build(self, task):
        # MacRun.__init__ は project.yml の worker（= 実在の Mac）と queue を要求する。この test が見るのは
        # preserve と main() の遷移だけなので、Run.__init__ だけ通して backend 側のフィールドは手で入れる
        r = object.__new__(MacRun)
        run.Run.__init__(r, "kumitate", str(task), "bug", str(self.ticket))
        self.assertEqual(r.base, "develop")
        work = self.ws / "work"; work.mkdir(exist_ok=True)
        r.work = str(work)
        r.env_file = str(work / "runtime.env")
        r.client = None
        r.run_lock = None
        r.state["backend"] = "macos-pull"
        env = {"SANDBOX_APP_DIR": str(self.app)}

        def sb(cmd, input_text=None, check=True):
            p = subprocess.run(["bash", "-c", cmd], text=True, capture_output=True, input=input_text,
                               env={**os.environ, **env})
            if check and p.returncode:
                raise RuntimeError(f"guest command failed ({p.returncode}): {p.stdout[-500:]}")
            return p.stdout

        self.done = []
        self.released = []

        def run_agent(step, retry_note=""):
            self.done.append(step["id"])
            if step["id"] == "implement" and len(self.done) == 2:
                (self.app / "fix.py").write_text("# 実装した直し\n", encoding="utf-8")
                git(self.app, "add", "fix.py"); git(self.app, "commit", "-q", "-m", "再現テストを直した")
            return True, ""

        def run_code(step):
            self.done.append(step["id"])
            return False, "FAIL base 側の時限切れテスト"      # gates は常に赤（max_loops を使い切る）

        r.sb = sb
        r.take = lambda: git(self.app, "checkout", "-q", "-B", r.branch, "origin/develop")
        r.refresh_token = lambda: None
        r.release = lambda: self.released.append(True)
        r.run_agent = run_agent
        r.run_code = run_code
        return r

    def test_a_human_run_pushes_the_work_branch_to_the_wip_branch(self):
        """gates を使い切って human: HEAD が wip ブランチに乗り、state.json にその名前が残る"""
        r = self.build(271)
        r.main()
        self.assertEqual(self.done, ["plan", "implement", "gates", "implement", "gates", "implement", "gates"])
        self.assertEqual(r.state["result"], "human")
        head = git_out(self.app, "rev-parse", "HEAD").strip()
        self.assertEqual(git_out(self.app, "log", "-1", "--format=%s").strip(), "再現テストを直した")
        # 完了条件: リモートに wip があり、作業ブランチのコミットと一致する
        self.assertEqual(git_out(self.origin, "rev-parse", "sandbox/271-bug-wip").strip(), head)
        self.assertEqual(r.state["wip_branch"], "sandbox/271-bug-wip")
        self.assertEqual(json.loads((r.run_dir / "state.json").read_text())["wip_branch"], "sandbox/271-bug-wip")
        self.assertEqual(self.released, [True])

    def test_a_failed_push_still_leaves_the_patch_and_releases(self):
        """push できなくても、差分は wip.patch に残り、成果物回収（release）は続く"""
        r = self.build(272)
        r.take = lambda: (git(self.app, "checkout", "-q", "-B", r.branch, "origin/develop"),
                          git(self.app, "remote", "set-url", "--push", "origin", str(self.ws / "missing.git")))
        r.main()
        self.assertEqual(r.state["result"], "human")
        self.assertEqual(r.state["wip_branch"], "")
        self.assertEqual(git_out(self.origin, "rev-parse", "--verify", "sandbox/272-bug-wip").strip(), "")
        patch = r.run_dir / "wip.patch"
        self.assertIn("fix.py", patch.read_text(encoding="utf-8"))
        # 人が引き取れること（log の約束どおり git am で復元できる）を、実際に当てて確かめる
        rescue = self.ws / "rescue"
        git(self.ws, "clone", "-q", str(self.origin), str(rescue))
        self.identity(rescue)
        git(rescue, "am", str(patch))
        self.assertEqual(git_out(rescue, "log", "-1", "--format=%s").strip(), "再現テストを直した")
        self.assertEqual((rescue / "fix.py").read_text(encoding="utf-8"), "# 実装した直し\n")
        self.assertEqual(self.released, [True])

    def test_a_failing_preserve_does_not_stop_the_release(self):
        """保全そのものが例外でも（token が払い出せない等）、外に投げず release まで進む"""
        r = self.build(273)
        def broken():
            raise RuntimeError("cannot mint project-scoped GitHub token")
        r.refresh_token = broken
        r.main()
        self.assertEqual(r.state["result"], "human")
        self.assertEqual(r.state["wip_branch"], "")
        self.assertEqual(self.released, [True])

    def test_a_control_database_failure_preserves_the_work_and_keeps_the_lease(self):
        """制御系 sqlite が落ちて run が human になるときも、コミット済みの実装を wip に残す（446）"""
        r = self.build(274)

        def boom(step, retry_note=""):
            self.done.append(step["id"])
            if step["id"] != "implement": return True, ""
            (self.app / "fix.py").write_text("# 実装した直し\n", encoding="utf-8")
            git(self.app, "add", "fix.py"); git(self.app, "commit", "-q", "-m", "再現テストを直した")
            raise sqlite3.OperationalError("database is locked")

        r.run_agent = boom
        self.assertEqual(r.main(), 2)
        self.assertEqual(self.done, ["plan", "implement"])
        self.assertEqual(r.state["result"], "human")
        self.assertEqual(r.state["error"], "database is locked")
        head = git_out(self.app, "rev-parse", "HEAD").strip()
        self.assertEqual(git_out(self.origin, "rev-parse", "sandbox/274-bug-wip").strip(), head)
        self.assertEqual(r.state["wip_branch"], "sandbox/274-bug-wip")
        self.assertEqual(json.loads((r.run_dir / "state.json").read_text())["wip_branch"], "sandbox/274-bug-wip")
        # lease は人が停まったゲストを検査できるよう残す（既存の意図。release で消さない）
        self.assertEqual(self.released, [])

    def test_a_preserve_that_fails_after_a_control_database_failure_is_not_fatal(self):
        """保全そのものが落ちても（ゲストが既に止まっている）、human の記録まで進む"""
        r = self.build(275)

        def boom(step, retry_note=""):
            self.done.append(step["id"])
            raise sqlite3.OperationalError("database is locked")

        r.run_agent = boom
        r.sb = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("worker is gone"))
        self.assertEqual(r.main(), 2)
        self.assertEqual(r.state["result"], "human")
        self.assertEqual(r.state["error"], "database is locked")
        self.assertEqual(r.state["wip_branch"], "")


if __name__ == "__main__":
    unittest.main()
