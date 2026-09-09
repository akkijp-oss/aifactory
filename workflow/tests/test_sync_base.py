"""PR を作る直前に base の最新を取り込む sync step（チケット 239）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も GitHub も使わない。一時 dir に bare の origin と clone（= VM の app dir）を作り、runner の
`sb` を「その clone の中で bash を回す」に差し替えて `main()` を回す（`test_take_failure.py` と同じ流儀で、
偽装するのは VM と agent だけ。step の遷移と git の実物はそのまま動かす）。

- base が進んでいない → review → sync → pr。マージコミットは作らない
- base が別ファイルで進んだ → sync が取り込んで PASS。gates は回し直さない
- 同じ行で衝突 → sync FAIL → implementer（resolve）に戻り、解消後 gates → review → sync → pr
- 3 回続けて解消できない → human。state.json の error と kb の note に理由が残る
- base と作業ブランチが同じ ADR 番号を採番 → マージは成功するが sync FAIL（番号の振り直しに戻す）
- auto_merge の無い PJ → automerge の工程は回らず、今までどおり PR を作って人間待ち（チケット 358）
- auto_merge のある PJ → automerge が merged.json を返せば state.json に merged が載り、kb はチケットを done にする
- auto_merge のある PJ で条件を満たさなかった → PR は開いたまま人間へ。error は automerge: で始まり、続きから回す口は付かない
"""
import datetime
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
KB = REPO / "kanban" / "bin" / "kb"

spec = importlib.util.spec_from_loader("sync_base_run", importlib.machinery.SourceFileLoader("sync_base_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
# 置き場だけ一時 dir に向ける。差し替えるのはこの test が読んだ runner のコピーの名前だけで、共有の
# aifactory_paths は書き換えない（他の test が同じプロセスで読んでいる）
run.paths = types.SimpleNamespace(project_dir=run.paths.project_dir, PROJECT_DIRS=run.paths.PROJECT_DIRS, RUNS=None)

TICKET = "# バグ: base 取り込みの再現\n\n並列に走った別 run が先にマージされた状態を作る。\n"
CHANGELOG = "# Changelog\n\n## [Unreleased]\n\n### Added\n- 最初の項目\n"


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if p.returncode: raise AssertionError(f"git {' '.join(args)} @ {cwd}: {p.stdout}{p.stderr}")
    return p.stdout


def git_out(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True).stdout


class SyncBaseTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        run.paths.RUNS = self.ws / "runs"
        self.ticket = self.ws / "ticket.md"; self.ticket.write_text(TICKET, encoding="utf-8")
        self.origin = self.ws / "origin.git"
        git(self.ws, "init", "-q", "--bare", "-b", "develop", str(self.origin))
        self.seed = self.ws / "seed"
        self.seed.mkdir()
        self.init_repo(self.seed, "develop")
        (self.seed / "README.md").write_text("kumitate\n", encoding="utf-8")
        (self.seed / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
        (self.seed / "shared.txt").write_text("base の行\nそのまま\n", encoding="utf-8")
        (self.seed / "docs" / "adr").mkdir(parents=True)
        (self.seed / "docs/adr/0001-a.md").write_text("# 0001\n", encoding="utf-8")
        git(self.seed, "add", "-A"); git(self.seed, "commit", "-q", "-m", "初期")
        git(self.seed, "remote", "add", "origin", str(self.origin))
        git(self.seed, "push", "-q", "-u", "origin", "develop")
        self.app = self.ws / "app"
        git(self.ws, "clone", "-q", str(self.origin), str(self.app))
        self.init_repo(self.app)

    def init_repo(self, d, branch=None):
        if branch: git(d, "init", "-q", "-b", branch)
        git(d, "config", "user.name", "aifactory test")
        git(d, "config", "user.email", "test@example.invalid")

    # ---------- 偽の VM
    def build(self, task, handlers, workflow="bug"):
        r = run.Run("kumitate", str(task), workflow, str(self.ticket))
        self.assertEqual(r.base, "develop")
        self.done = []; self.gates_n = 0; self.pr_n = 0
        self.vm_files = {}
        self.automerge = lambda step: (True, "")
        app = self.app

        def sb(cmd, input_text=None, check=True):
            p = subprocess.run(["bash", "-c", cmd], text=True, capture_output=True, input=input_text,
                               env={**os.environ, "SANDBOX_APP_DIR": str(app)})
            if check and p.returncode:
                raise RuntimeError(f"command failed ({p.returncode}): {cmd}\n{p.stderr[-500:]}")
            return p.stdout

        def run_agent(step, retry_note=""):
            self.done.append(step["id"])
            self.notes = getattr(self, "notes", {}); self.notes[step["id"]] = retry_note
            return handlers.get(step["id"], lambda s, n: (True, ""))(step, retry_note)

        def run_code(step):
            self.done.append(step["id"])
            if step["code"] == "sync-base": return run.Run.run_code(r, step)
            if step["code"] == "gates.sh": self.gates_n += 1; return True, "PASS すべて緑"
            if step["code"] == "pr-automerge.sh":
                ok, info = self.automerge(step)
                r.note_merged()                 # 本物の run_code と同じ順で merged.json を読ませる
                return ok, info
            self.pr_n += 1; return True, "https://example.invalid/pull/1"

        r.sb = sb
        r.take = lambda: git(app, "checkout", "-q", "-B", r.branch, "origin/develop")
        r.release = lambda: None
        r.preserve = lambda: ""
        r.refresh_token = lambda: None
        r.scp_to = lambda *a: None
        r.vm_read = lambda name: self.vm_files.get(name, "")
        r.run_agent = run_agent
        r.run_code = run_code
        return r

    def advance_base(self, path, text, message):
        """take の後で base が進む（並列に走った別 run の PR が先にマージされた状態）"""
        p = self.seed / path; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        git(self.seed, "add", "-A"); git(self.seed, "commit", "-q", "-m", message)
        git(self.seed, "push", "-q", "origin", "develop")

    def writes(self, path, text, message):
        """作業ブランチ側で 1 ファイル書いてコミットする agent"""
        def handler(step, note=""):
            p = self.app / path; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            git(self.app, "add", "-A"); git(self.app, "commit", "-q", "-m", message)
            return True, ""
        return handler

    def steps(self, r):
        return [h["step"] for h in r.state["history"]]

    # ---------- a: base が進んでいない
    def test_sync_runs_before_pr_and_adds_no_merge_when_base_is_unchanged(self):
        r = self.build(901, {"implement": self.writes("feature.txt", "新機能\n", "feature を足した")})
        r.main()
        self.assertEqual(self.steps(r), ["plan", "implement", "gates", "review", "sync", "pr"])
        self.assertEqual(git_out(self.app, "log", "--oneline", "--merges").strip(), "")
        self.assertEqual(self.pr_n, 1)

    # ---------- b: base が別ファイルで進んだ
    def test_sync_merges_an_advanced_base_without_rerunning_gates(self):
        self.advance_base("notes.txt", "別 run の変更\n", "先にマージされた別 run")
        r = self.build(902, {"implement": self.writes("feature.txt", "新機能\n", "feature を足した")})
        r.main()
        self.assertEqual(self.steps(r), ["plan", "implement", "gates", "review", "sync", "pr"])
        self.assertEqual(self.gates_n, 1)          # 衝突なしなら gates は回し直さない
        self.assertEqual(subprocess.run(["git", "merge-base", "--is-ancestor", "origin/develop", "HEAD"],
                                        cwd=str(self.app)).returncode, 0)
        self.assertTrue(git_out(self.app, "log", "--oneline", "--merges").strip())
        self.assertTrue((self.app / "notes.txt").exists())

    # ---------- c: 同じ行の衝突 → implementer に戻して解消
    def test_conflict_goes_back_to_the_implementer_and_then_reaches_pr(self):
        self.advance_base("shared.txt", "base 側の行\nそのまま\n", "base 側が 1 行目を直した")
        def resolve(step, note=""):
            subprocess.run(["git", "merge", "--no-edit", "origin/develop"], cwd=str(self.app), capture_output=True)
            (self.app / "shared.txt").write_text("base 側の行 + 作業ブランチの行\nそのまま\n", encoding="utf-8")
            git(self.app, "add", "shared.txt"); git(self.app, "commit", "-q", "--no-edit")
            return True, ""
        r = self.build(903, {"implement": self.writes("shared.txt", "作業ブランチの行\nそのまま\n", "1 行目を直した"),
                             "resolve": resolve})
        r.main()
        self.assertEqual(self.steps(r), ["plan", "implement", "gates", "review", "sync",
                                         "resolve", "gates", "review", "sync", "pr"])
        self.assertEqual(r.state["loops"]["sync->resolve"], 1)
        self.assertEqual(self.pr_n, 1)
        # 衝突したツリーを agent に渡さない: FAIL のあと作業ツリーは merge 状態で残っていない
        self.assertIn("shared.txt", self.notes["resolve"])

    # ---------- d: 解消できないまま 3 回 → human
    def test_three_failed_syncs_stop_at_a_human_with_the_reason(self):
        self.advance_base("shared.txt", "base 側の行\nそのまま\n", "base 側が 1 行目を直した")
        r = self.build(904, {"implement": self.writes("shared.txt", "作業ブランチの行\nそのまま\n", "1 行目を直した"),
                             "resolve": lambda s, n: (True, "")})
        r.main()
        self.assertEqual(self.done.count("sync"), 3)
        self.assertEqual(r.state["next"], "human")
        self.assertEqual(r.state["result"], "human")
        self.assertEqual(self.pr_n, 0)
        self.assertIn("sync", r.state["error"])
        self.assertIn("shared.txt", r.state["error"])
        # kb の note にも理由が残る（人間が板だけ見て分かる）
        env = dict(os.environ, AIFACTORY_WORKSPACE=str(self.ws))
        new = subprocess.run([sys.executable, str(KB), "new", "kumitate", "bug", "base 取り込みの再現",
                              "--body", "-", "--id", "904"], input=TICKET, text=True, capture_output=True, env=env)
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)
        name = r.run_dir.name
        subprocess.run([sys.executable, str(KB), "set", "904", "--run", name], capture_output=True, text=True, env=env)
        sync = subprocess.run([sys.executable, str(KB), "sync", "904"], capture_output=True, text=True, env=env)
        self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
        show = subprocess.run([sys.executable, str(KB), "show", "904"], text=True, capture_output=True, env=env)
        head = show.stdout.split("-" * 60)[0].splitlines()
        t = {l.split(" ", 1)[0]: l.split(" ", 1)[1].strip() for l in head if l.strip()}
        self.assertEqual(t["status"], "blocked", t)
        self.assertIn("sync", t["note"])

    # ---------- e: ADR の番号が別ファイルで重複した
    def test_duplicate_adr_numbers_fail_sync_even_though_the_merge_succeeds(self):
        self.advance_base("docs/adr/0003-base.md", "# 0003 base 側\n", "base 側が 0003 を採番")
        def resolve(step, note=""):
            git(self.app, "mv", "docs/adr/0003-branch.md", "docs/adr/0004-branch.md")
            git(self.app, "commit", "-q", "-m", "ADR を 0004 に振り直した")
            return True, ""
        r = self.build(905, {"implement": self.writes("docs/adr/0003-branch.md", "# 0003 作業ブランチ側\n", "ADR 0003 を足した"),
                             "resolve": resolve})
        r.main()
        self.assertEqual(self.steps(r), ["plan", "implement", "gates", "review", "sync",
                                         "resolve", "gates", "review", "sync", "pr"])
        self.assertIn("0003", self.notes["resolve"])
        self.assertEqual(self.pr_n, 1)

    # ---------- f: auto_merge が無い PJ は今までどおり PR で止まる（automerge の工程は回らない）
    def test_a_project_without_auto_merge_still_stops_at_a_human_after_the_pr(self):
        r = self.build(906, {"implement": self.writes("feature.txt", "新機能\n", "feature を足した")})
        self.assertIsNone(r.auto_merge)
        r.main()
        self.assertNotIn("automerge", self.steps(r))
        self.assertEqual(r.state["result"], "human")
        self.assertIsNone(r.state.get("merged"))

    # ---------- g: auto_merge のある PJ で条件が揃った
    def test_automerge_records_the_merge_and_the_ticket_becomes_done(self):
        r = self.build(907, {"implement": self.writes("feature.txt", "新機能\n", "feature を足した")})
        r.auto_merge = run.Run.normalize_auto_merge(True)
        self.vm_files["pr_url"] = "https://example.invalid/pull/7\n"
        self.vm_files["merged.json"] = json.dumps({"sha": "abc1234", "method": "merge", "base": "develop",
                                                   "pr_url": "https://example.invalid/pull/7", "at": "2026-09-09T12:00:00+09:00"})
        self.automerge = lambda step: (True, "MERGED: abc1234 https://example.invalid/pull/7")
        r.main()
        self.assertEqual(self.steps(r)[-2:], ["pr", "automerge"])
        self.assertEqual(r.state["result"], "end")
        self.assertEqual(r.state["merged"]["sha"], "abc1234")
        self.assertEqual(r.state["merged"]["base"], "develop")
        t = self.kb_sync(907, r)
        self.assertEqual(t["status"], "done", t)
        self.assertIn("自動マージ #7", t["note"])

    # ---------- h: auto_merge のある PJ だが条件を満たさなかった（PR は開いたまま人間へ）
    def test_automerge_that_does_not_merge_hands_the_open_pr_to_a_human_with_the_reason(self):
        r = self.build(908, {"implement": self.writes("feature.txt", "新機能\n", "feature を足した")})
        r.auto_merge = run.Run.normalize_auto_merge({"wait_min": 5})
        self.vm_files["pr_url"] = "https://example.invalid/pull/8\n"
        self.automerge = lambda step: (False, "poll 中\nNOMERGE: CI 赤 (test)")
        r.main()
        self.assertEqual(r.state["result"], "human")
        self.assertIsNone(r.state.get("merged"))
        self.assertEqual(r.state["error"], "automerge: CI 赤 (test)")
        self.assertIsNone(r.state.get("resume_step"))       # PR はできている。続きから回す対象ではない
        t = self.kb_sync(908, r)
        self.assertEqual(t["status"], "review", t)
        self.assertIn("/pull/8", t["note"])
        self.assertIn("CI 赤 (test)", t["note"])           # 板だけ見て「なぜ自動マージしなかったか」が読める

    def kb_sync(self, tid, r):
        """run の記録を kb に流し込み、チケットの状態とメモを読み返す"""
        env = dict(os.environ, AIFACTORY_WORKSPACE=str(self.ws))
        new = subprocess.run([sys.executable, str(KB), "new", "kumitate", "bug", "自動マージの再現",
                              "--body", "-", "--id", str(tid)], input=TICKET, text=True, capture_output=True, env=env)
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)
        subprocess.run([sys.executable, str(KB), "set", str(tid), "--run", r.run_dir.name], capture_output=True, text=True, env=env)
        sync = subprocess.run([sys.executable, str(KB), "sync", str(tid)], capture_output=True, text=True, env=env)
        self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
        show = subprocess.run([sys.executable, str(KB), "show", str(tid)], text=True, capture_output=True, env=env)
        head = show.stdout.split("-" * 60)[0].splitlines()
        return {l.split(" ", 1)[0]: l.split(" ", 1)[1].strip() for l in head if l.strip()}


if __name__ == "__main__":
    unittest.main()
