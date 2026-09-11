"""設定画面からのモデルの変更（チケット 416 / ADR-0063）。下見・保存・競合・検証を検査する。

  python3 -m unittest discover -s console/tests -p 'test_config_save.py' -v

- 実効モデルの答えは lib/aifactory_workflow.py（runner と共有）。期待表は workflow/tests/model_fixture.py と同じものを読む
- 書き込みは kit の一時コピーに対してだけ行う（リポジトリ本体の workflow/kit/ はテストで書き換えない）
- 既定は下見（1 バイトも書かない）。書くのは dry_run: false を明示したときだけ
- 版（sha256）が変わっていたら何も書かない。コメント・未知の行・並びは保存後も残る
- claude も VM も有料 API も使わない
"""
import hashlib
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "console" / "lib"))
sys.path.insert(0, str(REPO / "lib"))
import core  # noqa: E402
import aifactory_workflow as wfdef  # noqa: E402

# 期待表は runner 側と同じファイルを読む。sys.path には足さない（workflow/tests と console/tests には同じ名前のテストがあり、
# 足すと unittest の discover が別のディレクトリの同名 module を掴む）
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("model_fixture", REPO / "workflow" / "tests" / "model_fixture.py")
fx = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(fx)


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


class KitTestCase(unittest.TestCase):
    """kit の一時コピーに core の書き先を差し替える（本体の workflow/kit/ は触らない）"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="cfgsave-"))
        shutil.copytree(REPO / "workflow" / "kit", self.tmp / "kit")
        self.saved = {k: getattr(core, k) for k in ("KIT", "WORKFLOWS", "WORKFLOW_SCHEMA", "LOGS", "RUNS")}
        core.KIT = self.tmp / "kit"
        core.WORKFLOWS = core.KIT / "workflows"
        core.WORKFLOW_SCHEMA = core.KIT / "schema" / "workflow.schema.json"
        core.LOGS = self.tmp / "logs"
        core.RUNS = self.tmp / "runs"
        self.routes = core.KIT / "routes.env"
        self.feature = core.WORKFLOWS / "feature.yml"
        self.addCleanup(self.restore)

    def restore(self):
        for k, v in self.saved.items(): setattr(core, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def apply(self, **b):
        return core.config_model_apply(b)

    def save(self, **b):
        """下見 → その版で保存、の 2 段（画面と同じ順）"""
        pre = self.apply(**b)
        return self.apply(**b, dry_run=False, base_sha256=pre["base_sha256"])


class ResolutionIsSharedTest(unittest.TestCase):
    """runner と同じ期待表（workflow/tests/model_fixture.py）で console 側の解決も検査する"""

    def test_every_case_of_the_shared_fixture(self):
        for sid, env, routes, want in fx.CASES:
            got = wfdef.resolve_model(fx.step(sid), fx.routes_of(routes), env or None)
            self.assertEqual({k: got[k] for k in want}, want, f"{sid} env={env}")

    def test_the_key_family_of_each_model(self):
        for model, fam in fx.FAMILIES:
            self.assertEqual(wfdef.token_family(model), fam, model)


class PreviewTest(KitTestCase):
    def test_the_preview_writes_nothing_and_shows_both_sides(self):
        before = sha(self.routes)
        d = self.apply(target="routes", key="MODEL_judgment", value="claude-opus-5")
        self.assertTrue(d["dry_run"])
        self.assertFalse(d["written"])
        self.assertEqual(sha(self.routes), before)                    # 1 バイトも書いていない
        self.assertEqual(d["base_sha256"], before)
        moved = {(a["workflow"], a["step"]) for a in d["affected"]}
        self.assertTrue(moved)
        for a in d["affected"]:
            self.assertEqual((a["before"], a["after"]), ("claude-fable-5-1", "claude-opus-5"))
            self.assertEqual(a["before_class"], "judgment")

    def test_a_shared_route_lists_every_step_of_that_class_in_every_workflow(self):
        d = self.apply(target="routes", key="MODEL_judgment", value="claude-opus-5")
        rows = core.model_rows()
        want = {(r["workflow"], r["step"]) for r in rows if r["model_class"] == "judgment" and r["model_from"] == "routes"}
        self.assertEqual({(a["workflow"], a["step"]) for a in d["affected"]}, want)
        self.assertGreater(len(want), 1)
        self.assertIn("共通の設定です", d["warning"])

    def test_pinning_one_step_moves_only_that_step(self):
        d = self.apply(target="step", workflow="feature", step="design", key="model", value="claude-haiku-4-5-20251001")
        self.assertEqual([(a["workflow"], a["step"]) for a in d["affected"]], [("feature", "design")])
        self.assertEqual(d["affected"][0]["after_from"], "step")

    def test_the_preview_says_what_is_running_and_when_it_takes_effect(self):
        d = self.apply(target="routes", key="MODEL_coding", value="claude-sonnet-5")
        self.assertEqual(d["applies_to"], "next_run")
        self.assertIsInstance(d["running_runs"], list)


class SaveTest(KitTestCase):
    def test_saving_a_route_keeps_comments_and_unknown_lines(self):
        text = self.routes.read_text(encoding="utf-8")
        self.routes.write_text(text + "MODEL_experiment=claude-sonnet-5\n# 2026-09-10 別の人の覚え書き\n", encoding="utf-8")
        d = self.save(target="routes", key="MODEL_judgment", value="claude-opus-5")
        self.assertTrue(d["written"])
        after = self.routes.read_text(encoding="utf-8")
        self.assertIn("MODEL_judgment=claude-opus-5\n", after)
        self.assertIn("# 2026-09-10 別の人の覚え書き", after)
        self.assertIn("MODEL_experiment=claude-sonnet-5", after)
        self.assertIn("# workflow/kit/routes.env", after)              # 冒頭のコメントが残る
        self.assertEqual(core.model_routes()["MODEL_judgment"], "claude-opus-5")
        self.assertEqual(d["sha_after"], sha(self.routes))
        self.assertTrue(pathlib.Path(core.REPO / d["backup"]).is_file() or pathlib.Path(d["backup"]).is_file())

    def test_the_saved_value_and_the_effective_value_agree_after_saving(self):
        d = self.save(target="step", workflow="feature", step="design", key="model", value="claude-sonnet-5")
        row = next(r for r in core.model_rows() if (r["workflow"], r["step"]) == ("feature", "design"))
        self.assertEqual((row["model"], row["model_from"]), ("claude-sonnet-5", "step"))
        self.assertEqual(row["model"], next(a["after"] for a in d["affected"] if a["step"] == "design"))
        detail = core.workflow_detail("feature")
        st = next(s for s in detail["steps"] if s["id"] == "design")
        self.assertEqual(st["model"], "claude-sonnet-5")               # 保存値
        self.assertEqual(st["model_resolved"]["model"], "claude-sonnet-5")   # 実効値
        self.assertEqual(st["unknown_keys"], [])                       # schema が認めたキー

    def test_going_back_to_inheritance_removes_the_line(self):
        self.save(target="step", workflow="feature", step="design", key="model", value="claude-sonnet-5")
        before_lines = self.feature.read_text(encoding="utf-8").count("\n")
        d = self.save(target="step", workflow="feature", step="design", key="model", value=None)
        self.assertTrue(d["written"])
        self.assertNotIn("model: claude-sonnet-5", self.feature.read_text(encoding="utf-8"))
        self.assertEqual(self.feature.read_text(encoding="utf-8").count("\n"), before_lines - 1)
        row = next(r for r in core.model_rows() if (r["workflow"], r["step"]) == ("feature", "design"))
        self.assertEqual(row["model_from"], "routes")                  # 共通経路の継承に戻った

    def test_the_step_class_can_be_changed_and_the_rest_of_the_definition_is_untouched(self):
        import yaml
        before = yaml.safe_load(self.feature.read_text(encoding="utf-8"))
        self.save(target="step", workflow="feature", step="design", key="model_class", value="coding")
        after = yaml.safe_load(self.feature.read_text(encoding="utf-8"))
        for s in before["steps"]:
            if s["id"] == "design": s["model_class"] = "coding"
        self.assertEqual(after, before)                                # 当該キー以外は 1 つも動いていない
        self.assertIn("# feature: 機能追加", self.feature.read_text(encoding="utf-8"))

    def test_changing_one_step_does_not_move_the_other_steps(self):
        before = {(r["workflow"], r["step"]): r["model"] for r in core.model_rows()}
        self.save(target="step", workflow="feature", step="design", key="model", value="claude-haiku-4-5-20251001")
        after = {(r["workflow"], r["step"]): r["model"] for r in core.model_rows()}
        self.assertEqual([k for k in before if before[k] != after[k]], [("feature", "design")])

    def test_an_audit_line_is_written_only_when_the_file_changed(self):
        self.assertEqual(core.config_changes(), [])
        self.apply(target="routes", key="MODEL_coding", value="claude-sonnet-5")     # 下見だけ
        self.assertEqual(core.config_changes(), [])
        self.save(target="routes", key="MODEL_coding", value="claude-sonnet-5")
        rec = core.config_changes()
        self.assertEqual(len(rec), 1)
        self.assertEqual((rec[0]["key"], rec[0]["before"], rec[0]["after"]), ("MODEL_coding", "claude-opus-5", "claude-sonnet-5"))
        self.assertTrue(rec[0]["backup"] and rec[0]["at"])
        self.assertNotEqual(rec[0]["sha_before"], rec[0]["sha_after"])

    def test_saving_the_same_value_writes_nothing(self):
        before = sha(self.routes)
        d = self.save(target="routes", key="MODEL_coding", value="claude-opus-5")
        self.assertFalse(d["written"])
        self.assertEqual(sha(self.routes), before)
        self.assertIn("いまと同じ設定", d["warning"])

    def test_the_permission_of_the_target_file_is_kept(self):
        """mkstemp は 0600 で作る。置き換えで元の permission を狭めない（他の人・他のプロセスが読めなくなる）"""
        self.routes.chmod(0o644)
        self.feature.chmod(0o664)
        self.save(target="routes", key="MODEL_judgment", value="claude-sonnet-5")
        self.save(target="step", workflow="feature", step="design", key="model", value="claude-sonnet-5")
        self.assertEqual(stat.S_IMODE(self.routes.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(self.feature.stat().st_mode), 0o664)

    def test_a_step_whose_brief_has_bullets_can_still_be_changed(self):
        """brief の中の "- " 行は工程の境目ではない。そこより後ろにある model: も差し替え・削除できる"""
        p = core.WORKFLOWS / "merge-pr.yml"
        text = p.read_text(encoding="utf-8")
        self.assertIn("\n      - ", text)                               # brief の中に入れ子の箇条書きがある
        p.write_text(text.replace("    inputs: [report.md, gates.txt]\n",
                                  "    model: claude-sonnet-5\n    inputs: [report.md, gates.txt]\n"), encoding="utf-8")
        d = self.save(target="step", workflow="merge-pr", step="review", key="model", value="claude-fable-5-1")
        self.assertTrue(d["written"], d.get("warning"))
        after = p.read_text(encoding="utf-8")
        self.assertEqual(after.count("    model: "), 1)                  # 重複して挿し込まれていない
        self.assertIn("    model: claude-fable-5-1\n", after)
        row = next(r for r in core.model_rows() if (r["workflow"], r["step"]) == ("merge-pr", "review"))
        self.assertEqual((row["model"], row["model_from"]), ("claude-fable-5-1", "step"))
        d = self.save(target="step", workflow="merge-pr", step="review", key="model", value=None)
        self.assertTrue(d["written"], d.get("warning"))
        self.assertNotIn("    model: ", p.read_text(encoding="utf-8"))   # 継承に戻せる
        self.assertIn("      - 解消で", p.read_text(encoding="utf-8"))   # brief はそのまま

    def test_the_backup_holds_the_previous_content(self):
        before = self.routes.read_text(encoding="utf-8")
        d = self.save(target="routes", key="MODEL_judgment", value="claude-sonnet-5")
        bak = pathlib.Path(d["backup"])
        if not bak.is_absolute(): bak = core.REPO / bak
        self.assertEqual(bak.read_text(encoding="utf-8"), before)      # 元の設定へ戻せる


class RefusalTest(KitTestCase):
    def refuse(self, **b):
        before = (sha(self.routes), sha(self.feature))
        with self.assertRaises(core.ApiError) as cm:
            pre = None
            try: pre = self.apply(**b)
            except core.ApiError: raise
            self.apply(**b, dry_run=False, base_sha256=pre["base_sha256"])
        self.assertEqual((sha(self.routes), sha(self.feature)), before, "何も書いていないこと")
        return cm.exception

    def test_a_key_outside_the_allowlist_is_refused(self):
        for key in ("MODEL_experiment", "PATH", "CLAUDE_CODE_OAUTH_TOKEN", "MODEL_judgment\nPATH=x"):
            self.refuse(target="routes", key=key, value="claude-opus-5")

    def test_a_model_name_with_a_strange_shape_is_refused(self):
        for v in ("claude opus", "../../etc/passwd", "claude-opus-5\nMODEL_coding=x", "x" * 80, "claude/opus"):
            self.refuse(target="routes", key="MODEL_coding", value=v)

    def test_a_model_of_an_unknown_family_is_refused(self):
        e = self.refuse(target="routes", key="MODEL_coding", value="gpt-9")
        self.assertIn("鍵の系統", str(e))

    def test_a_key_other_than_model_and_model_class_is_refused(self):
        for key in ("timeout_min", "role", "brief", "code"):
            self.refuse(target="step", workflow="feature", step="design", key=key, value="claude-opus-5")

    def test_a_code_step_and_an_unknown_step_are_refused(self):
        self.refuse(target="step", workflow="feature", step="gates", key="model", value="claude-opus-5")
        self.refuse(target="step", workflow="feature", step="nosuch", key="model", value="claude-opus-5")
        self.refuse(target="step", workflow="nosuch", step="design", key="model", value="claude-opus-5")

    def test_an_unknown_class_is_refused(self):
        self.refuse(target="step", workflow="feature", step="design", key="model_class", value="wizard")

    def test_an_unknown_target_is_refused(self):
        self.refuse(target="anything", key="MODEL_coding", value="claude-opus-5")

    def test_a_file_changed_by_someone_else_is_not_overwritten(self):
        pre = self.apply(target="routes", key="MODEL_judgment", value="claude-opus-5")
        self.routes.write_text(self.routes.read_text(encoding="utf-8").replace("MODEL_research=claude-sonnet-5",
                                                                               "MODEL_research=claude-haiku-4-5-20251001"), encoding="utf-8")
        outside = sha(self.routes)
        with self.assertRaises(core.ApiError) as cm:
            self.apply(target="routes", key="MODEL_judgment", value="claude-opus-5", dry_run=False, base_sha256=pre["base_sha256"])
        self.assertEqual(cm.exception.code, 409)
        self.assertEqual(sha(self.routes), outside)                    # 他の人の変更を上書きしていない
        self.assertEqual(core.model_routes()["MODEL_research"], "claude-haiku-4-5-20251001")

    def test_saving_without_a_version_is_refused(self):
        with self.assertRaises(core.ApiError):
            self.apply(target="routes", key="MODEL_judgment", value="claude-opus-5", dry_run=False)

    def test_the_string_false_is_still_a_preview(self):
        before = sha(self.routes)
        d = self.apply(target="routes", key="MODEL_judgment", value="claude-opus-5", dry_run="false")
        self.assertTrue(d["dry_run"])
        self.assertEqual(sha(self.routes), before)


class RunningRunTest(KitTestCase):
    """実行中の run は起動時に読んだ経路表で動く（保存しても途中で変わらない）"""

    def test_a_run_that_already_read_the_routes_keeps_its_model(self):
        import importlib.machinery, importlib.util
        spec = importlib.util.spec_from_loader("cfgsave_run", importlib.machinery.SourceFileLoader("cfgsave_run", str(REPO / "workflow/bin/run")))
        run = importlib.util.module_from_spec(spec); spec.loader.exec_module(run)
        r = run.Run.__new__(run.Run)
        r.routes = core.model_routes()                       # Run.__init__ と同じで、起動のときに 1 回だけ読む
        step = {"id": "design", "role": "planner"}
        was = r.model_for(step)
        self.save(target="routes", key="MODEL_judgment", value="claude-opus-5")
        self.assertEqual(r.model_for(step), was)             # 走っている run の答えは変わらない
        r2 = run.Run.__new__(run.Run); r2.routes = core.model_routes()
        self.assertEqual(r2.model_for(step)[0], "claude-opus-5")   # 次に起こす run（resume も別プロセス）は新しい値を読む


@unittest.skipUnless(shutil.which("node"), "node が無い")
class RenderTest(KitTestCase):
    """編集の欄と下見の中身（app.js の部品を node で直に動かす）"""

    def js(self, probe):
        import json as _json, subprocess
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        from test_console import js_line, js_block
        static = REPO / "console" / "static"
        app = (static / "app.js").read_text(encoding="utf-8")
        w = core.workflow_detail("feature")
        d = {"routes": core.model_routes(), "model_edit": core.model_edit_view()}
        src = self.tmp / "cfg.js"
        src.write_text("\n".join([
            (static / "strings.js").read_text(encoding="utf-8"),
            *[js_line(app, n) for n in ("esc", "tt", "pad", "fmtT", "tzLabel", "cfgOpts", "cfgAffectedRows")],
            *[js_block(app, n) for n in ("cfgModelPreview", "cfgModelEdit")],
            f"const wf = {_json.dumps(w, ensure_ascii=False)};",
            f"const d = {_json.dumps(d, ensure_ascii=False)};",
            "const steps = {}; wf.steps.forEach(s => steps[s.id] = s);",
            "const out = {};",
            *[f"out[{k!r}] = {v};" for k, v in probe.items()],
            "console.log(JSON.stringify(out));"]), encoding="utf-8")
        r = subprocess.run(["node", str(src)], text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return _json.loads(r.stdout)

    def test_the_panel_offers_the_three_scopes_and_going_back_to_inheritance(self):
        html = self.js({"edit": "cfgModelEdit(wf, steps.design, d)"})["edit"]
        self.assertIn('data-target="step" data-key="model"', html)
        self.assertIn('data-target="step" data-key="model_class"', html)
        self.assertIn('data-target="routes" data-key="MODEL_judgment"', html)
        self.assertNotIn("data-inherit", html)                      # 指定が無いので「継承へ戻す」は出さない
        self.assertIn("claude-fable-5-1", html)                     # 共通の経路のいまの値
        self.assertIn(self.T()["config"]["modelEdit"], html)          # 見出しは文言の集約から出す

    def test_the_inherit_button_appears_once_the_step_has_its_own_value(self):
        self.save(target="step", workflow="feature", step="design", key="model", value="claude-sonnet-5")
        html = self.js({"edit": "cfgModelEdit(wf, steps.design, d)"})["edit"]
        self.assertIn('data-inherit="1"', html)
        self.assertIn('value="claude-sonnet-5"', html)

    def test_the_preview_shows_the_affected_steps_and_says_it_is_a_shared_setting(self):
        p = self.apply(target="routes", key="MODEL_judgment", value="claude-opus-5")
        html = self.js({"pre": f"cfgModelPreview({json.dumps(p, ensure_ascii=False)})"})["pre"]
        self.assertIn("claude-fable-5-1", html)
        self.assertIn("claude-opus-5", html)
        for a in p["affected"]: self.assertIn(f">{a['step']}<", html)
        self.assertIn("共通の設定です", html)
        self.assertIn("commit しません", html)                      # 未コミットの注意

    def test_the_preview_of_one_step_does_not_claim_a_shared_change(self):
        p = self.apply(target="step", workflow="feature", step="design", key="model", value="claude-opus-5")
        html = self.js({"pre": f"cfgModelPreview({json.dumps(p, ensure_ascii=False)})"})["pre"]
        self.assertNotIn("共通の設定です", html)
        self.assertIn(">design<", html)

    def T(self):
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        return __import__("test_strings").load()


if __name__ == "__main__":
    unittest.main()
