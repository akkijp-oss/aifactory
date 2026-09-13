"""設定画面からの「工程の yml ブロックごと」の変更（チケット 503 / ADR-0065 / ADR-0073）。

  python3 -m unittest discover -s console/tests -p 'test_config_block.py' -v

- 書き込みは kit の一時コピーに対してだけ行う（リポジトリ本体の workflow/kit/ はテストで書き換えない。KitTestCase）
- 書くのは当該工程のブロックだけ。その前後は 1 バイトも変わらない（コメント・並び・引用が保たれることの根拠）
- 検査に落ちた入力では 1 バイトも書かない（控えも変更記録も残らない）
- 工程の id と担い手の種類（role / code）はここでは変えられない（決めた線）
- claude も VM も有料 API も使わない
"""
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_config_save import KitTestCase, REPO, core, sha  # noqa: E402


def bounds(text, step_id):
    lines = text.splitlines(keepends=True)
    item, end, _ = core.step_block_bounds(lines, step_id)
    return "".join(lines[:item]), "".join(lines[item:end]), "".join(lines[end:])


class BlockTestCase(KitTestCase):
    """ブロック編集の出発点。工程の原文を取り、書いたあとの前後がバイトで一致することを見る"""

    def block(self, step_id, wf="feature"):
        return core.step_block_text((core.WORKFLOWS / f"{wf}.yml").read_text(encoding="utf-8"), step_id)

    def save_block(self, step, text, wf="feature"):
        return self.save(target="block", workflow=wf, step=step, text=text)

    def assert_only_this_block_moved(self, step, before_text, wf="feature"):
        """当該ブロック以外が 1 文字も変わっていないこと（前半と後半をバイトで比べる）"""
        after_text = (core.WORKFLOWS / f"{wf}.yml").read_text(encoding="utf-8")
        pre, _, suf = bounds(before_text, step)
        self.assertTrue(after_text.startswith(pre), "ファイルの先頭（コメント・name・description）が変わっている")
        self.assertTrue(after_text.endswith(suf), "うしろの工程が変わっている")
        return after_text


class ReadTest(BlockTestCase):
    def test_every_step_carries_its_own_block_and_nothing_else(self):
        """画面に渡す yaml_block は、その工程のブロックの原文（行末コメントも含む）。ファイル全体は出さない"""
        w = core.workflow_detail("feature")
        text = (core.WORKFLOWS / "feature.yml").read_text(encoding="utf-8")
        for s in w["steps"]:
            self.assertIsNotNone(s["yaml_block"], s["id"])
            self.assertIn(s["yaml_block"], text)
            self.assertTrue(s["yaml_block"].lstrip().startswith(f"- id: {s['id']}"), s["id"])
        design = next(s for s in w["steps"] if s["id"] == "design")
        self.assertIn("# 計画は Fable", design["yaml_block"])             # 行末コメントが残っている
        self.assertNotIn("name: feature", design["yaml_block"])           # ファイル全体は出さない
        self.assertNotIn("- id: implement", design["yaml_block"])         # 次の工程も出さない

    def test_the_first_and_the_last_step_have_their_own_bounds(self):
        w = core.workflow_detail("feature")
        first, last = w["steps"][0], w["steps"][-1]
        self.assertEqual((first["id"], last["id"]), ("research", "automerge"))
        self.assertIn("role: researcher", first["yaml_block"])
        self.assertIn("code: pr-automerge.sh", last["yaml_block"])       # EOF までが最後の工程のブロック
        self.assertNotIn("- id: design", first["yaml_block"])

    def test_a_step_whose_brief_has_bullets_keeps_its_bounds(self):
        """brief の中の "- " 行は工程の境目ではない（ブロックの切り出しでも同じ規則）"""
        w = core.workflow_detail("merge-pr")
        review = next(s for s in w["steps"] if s["id"] == "review")
        self.assertIn("\n      - ", review["yaml_block"])
        self.assertNotIn("- id: merge", review["yaml_block"])


class SaveTest(BlockTestCase):
    def test_a_key_the_form_does_not_have_can_be_changed(self):
        """モデル以外（timeout_min）もブロックから直せる。書かれるのは当該ブロックだけ"""
        before = (core.WORKFLOWS / "feature.yml").read_text(encoding="utf-8")
        b = self.block("design")
        self.assertNotIn("timeout_min:", b)
        d = self.save_block("design", b.replace("    role: planner\n", "    role: planner\n    timeout_min: 45\n"))
        self.assertTrue(d["written"], d.get("warning"))
        self.assertEqual(d["step_diff"], [{"key": "timeout_min", "before": None, "after": 45}])
        after = self.assert_only_this_block_moved("design", before)
        self.assertIn("    timeout_min: 45\n", after)
        self.assertIn("# 計画は Fable", after)                            # ブロックの中のコメントも残る
        st = next(s for s in core.workflow_detail("feature")["steps"] if s["id"] == "design")
        self.assertEqual((st["timeout_min"], st["timeout_default"]), (45, False))

    def test_the_comments_and_the_order_of_the_whole_file_survive(self):
        before = (core.WORKFLOWS / "feature.yml").read_text(encoding="utf-8")
        self.save_block("design", self.block("design").replace("model: claude-fable-5-1", "model: claude-opus-5"))
        after = (core.WORKFLOWS / "feature.yml").read_text(encoding="utf-8")
        self.assertEqual([l for l in before.splitlines() if l.lstrip().startswith("#")],
                         [l for l in after.splitlines() if l.lstrip().startswith("#")])   # 全部のコメント行が残る
        self.assertEqual([l for l in before.splitlines() if l.startswith("  - id: ")],
                         [l for l in after.splitlines() if l.startswith("  - id: ")])     # 工程の並びも変わらない
        self.assertEqual(len(before.splitlines()), len(after.splitlines()))

    def test_the_first_and_the_last_step_can_be_saved(self):
        for step in ("research", "automerge"):
            before = (core.WORKFLOWS / "feature.yml").read_text(encoding="utf-8")
            b = self.block(step)
            d = self.save_block(step, b.rstrip("\n") + "\n    timeout_min: 20\n")
            self.assertTrue(d["written"], (step, d.get("warning")))
            after = self.assert_only_this_block_moved(step, before)
            self.assertIn("    timeout_min: 20\n", after)

    def test_a_step_whose_brief_has_bullets_can_be_saved(self):
        p = core.WORKFLOWS / "merge-pr.yml"
        before = p.read_text(encoding="utf-8")
        b = self.block("review", wf="merge-pr")
        d = self.save_block("review", b.replace("    role: reviewer\n", "    role: reviewer\n    model: claude-fable-5-1\n"), wf="merge-pr")
        self.assertTrue(d["written"], d.get("warning"))
        after = self.assert_only_this_block_moved("review", before, wf="merge-pr")
        self.assertIn("      - 解消で", after)                            # brief の入れ子はそのまま
        row = next(r for r in core.model_rows() if (r["workflow"], r["step"]) == ("merge-pr", "review"))
        self.assertEqual((row["model"], row["model_from"]), ("claude-fable-5-1", "step"))

    def test_removing_the_model_line_goes_back_to_inheritance(self):
        b = self.block("design")
        d = self.apply(target="block", workflow="feature", step="design",
                       text="".join(l for l in b.splitlines(keepends=True) if not l.startswith("    model:")))
        self.assertEqual([(a["workflow"], a["step"]) for a in d["affected"]], [("feature", "design")])
        self.assertEqual(d["affected"][0]["after_from"], "routes")        # 経路の値（継承）に戻る
        self.assertEqual([c["key"] for c in d["step_diff"]], ["model"])
        self.assertIsNone(d["step_diff"][0]["after"])

    def test_changing_the_class_moves_the_route_of_this_step(self):
        b = self.block("design")
        d = self.apply(target="block", workflow="feature", step="design",
                       text="".join(l for l in b.splitlines(keepends=True) if not l.startswith("    model:"))
                            .replace("    role: planner\n", "    role: planner\n    model_class: research\n"))
        self.assertEqual(d["affected"][0]["before_class"], "judgment")
        self.assertEqual(d["affected"][0]["after_class"], "research")

    def test_the_role_may_change_within_the_schema(self):
        before = (core.WORKFLOWS / "feature.yml").read_text(encoding="utf-8")
        d = self.save_block("design", self.block("design").replace("role: planner", "role: reviewer"))
        self.assertTrue(d["written"], d.get("warning"))
        self.assert_only_this_block_moved("design", before)
        self.assertEqual([c for c in d["step_diff"] if c["key"] == "role"],
                         [{"key": "role", "before": "planner", "after": "reviewer"}])

    def test_saving_the_same_block_writes_nothing(self):
        before = sha(self.feature)
        d = self.save_block("design", self.block("design"))
        self.assertFalse(d["written"])
        self.assertEqual(sha(self.feature), before)
        self.assertFalse((core.LOGS / core.CONFIG_CHANGES).exists())

    def test_the_backup_and_the_audit_line_hold_what_changed(self):
        before = (core.WORKFLOWS / "feature.yml").read_text(encoding="utf-8")
        d = self.save_block("design", self.block("design").replace("    role: planner\n", "    role: planner\n    timeout_min: 45\n"))
        bak = pathlib.Path(d["backup"])
        if not bak.is_absolute(): bak = core.REPO / bak
        self.assertEqual(bak.read_text(encoding="utf-8"), before)         # 控えは書く前の本文そのもの
        lines = (core.LOGS / core.CONFIG_CHANGES).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        a = json.loads(lines[0])
        self.assertEqual((a["target"], a["key"], a["workflow"], a["step"]), ("block", "block", "feature", "design"))
        self.assertEqual(a["changes"], [{"key": "timeout_min", "before": None, "after": 45}])
        self.assertIn("timeout_min: 45", a["after"])                      # 記録は読める形（原文を丸ごと残さない）
        self.assertNotIn("brief", str(a["after"]))
        self.assertIsInstance(d["git"], (str, type(None)))

    def test_the_preview_writes_nothing(self):
        before = sha(self.feature)
        d = self.apply(target="block", workflow="feature", step="design",
                       text=self.block("design").replace("    role: planner\n", "    role: planner\n    timeout_min: 45\n"))
        self.assertTrue(d["dry_run"])
        self.assertFalse(d["written"])
        self.assertEqual(sha(self.feature), before)
        self.assertEqual(d["base_sha256"], before)
        self.assertEqual(d["applies_to"], "next_run")                     # 動いている run には反映しない
        self.assertFalse((core.LOGS / core.CONFIG_CHANGES).exists())


class BlockRefusalTest(BlockTestCase):
    """検査に落ちる入力。落ちたら 1 バイトも書かない（控えも変更記録も残らない）"""

    def refuse(self, step, text, wf="feature"):
        p = core.WORKFLOWS / f"{wf}.yml"
        before = sha(p)
        with self.assertRaises(core.ApiError) as cm:
            pre = self.apply(target="block", workflow=wf, step=step, text=text)
            self.apply(target="block", workflow=wf, step=step, text=text, dry_run=False, base_sha256=pre["base_sha256"])
        self.assertEqual(sha(p), before, "何も書いていないこと")
        self.assertEqual(sorted(q.name for q in p.parent.glob("*.bak-*")), [], "控えも残っていないこと")
        self.assertFalse((core.LOGS / core.CONFIG_CHANGES).exists(), "変更記録も残っていないこと")
        return cm.exception

    def test_yaml_that_cannot_be_read_is_refused(self):
        e = self.refuse("design", self.block("design").replace("    role: planner\n", "   role: planner\n  x: [\n"))
        self.assertIn("書きませんでした", str(e))

    def test_a_value_of_the_wrong_type_is_refused(self):
        e = self.refuse("design", self.block("design").replace("    role: planner\n", "    role: planner\n    timeout_min: abc\n"))
        self.assertIn("schema", str(e))
        self.assertIn("timeout_min", str(e))                              # どこが悪いかを path: message で出す

    def test_an_unknown_key_is_refused(self):
        e = self.refuse("design", self.block("design").replace("    role: planner\n", "    role: planner\n    foo: 1\n"))
        self.assertIn("schema", str(e))

    def test_an_unknown_role_is_refused(self):
        self.refuse("design", self.block("design").replace("role: planner", "role: wizard"))

    def test_changing_the_id_is_refused(self):
        e = self.refuse("design", self.block("design").replace("- id: design", "- id: design2"))
        self.assertIn("id はここでは変えられません", str(e))

    def test_changing_who_runs_the_step_is_refused(self):
        e = self.refuse("design", self.block("design").replace("    role: planner\n", "    code: gates.sh\n"))
        self.assertIn("担い手の種類", str(e))
        e = self.refuse("gates", self.block("gates").replace("    code: gates.sh\n", "    role: implementer\n"))
        self.assertIn("担い手の種類", str(e))

    def test_two_steps_in_one_block_are_refused(self):
        e = self.refuse("design", self.block("design") + "  - id: design3\n    role: planner\n")
        self.assertIn("この工程のブロック 1 つだけ", str(e))

    def test_deleting_the_step_is_refused(self):
        self.refuse("design", "  - id: design\n")                         # role も code も無い（schema で落ちる）
        e = self.refuse("design", "\n")
        self.assertIn("空です", str(e))

    def test_a_block_that_does_not_start_with_the_id_line_is_refused(self):
        b = self.block("design").splitlines(keepends=True)
        e = self.refuse("design", b[1] + b[0] + "".join(b[2:]))            # role を先に書いた
        self.assertIn("- id: design", str(e))

    def test_a_block_indented_out_of_the_step_list_is_refused(self):
        self.refuse("design", self.block("design").replace("\n    ", "\n  ").replace("  - id:", "- id:"))

    def test_a_model_of_an_unknown_family_is_refused(self):
        e = self.refuse("design", self.block("design").replace("model: claude-fable-5-1", "model: gpt-9"))
        self.assertIn("鍵の系統", str(e))

    def test_an_unknown_step_or_workflow_is_refused(self):
        self.refuse("nosuch", "  - id: nosuch\n    role: planner\n")
        with self.assertRaises(core.ApiError):
            self.apply(target="block", workflow="nosuch", step="design", text="  - id: design\n")

    def test_a_block_that_is_too_long_is_refused(self):
        e = self.refuse("design", self.block("design").replace("role: planner", "role: planner\n    brief: |\n      " + "x" * 70000))
        self.assertIn("KiB", str(e))

    def test_saving_without_the_version_is_refused(self):
        before = sha(self.feature)
        with self.assertRaises(core.ApiError) as cm:
            self.apply(target="block", workflow="feature", step="design", dry_run=False,
                       text=self.block("design").replace("role: planner", "role: reviewer"))
        self.assertIn("base_sha256", str(cm.exception))
        self.assertEqual(sha(self.feature), before)

    def test_a_file_changed_by_someone_else_is_not_overwritten(self):
        b = self.block("design").replace("role: planner", "role: reviewer")
        pre = self.apply(target="block", workflow="feature", step="design", text=b)
        self.feature.write_text(self.feature.read_text(encoding="utf-8").replace("- id: gates", "- id: gates  # 誰かが直した"),
                                encoding="utf-8")
        moved = sha(self.feature)
        with self.assertRaises(core.ApiError) as cm:
            self.apply(target="block", workflow="feature", step="design", text=b, dry_run=False, base_sha256=pre["base_sha256"])
        self.assertEqual(cm.exception.code, 409)
        self.assertEqual(sha(self.feature), moved, "409 のときは何も書かない")


class RepoIsNotTouchedTest(unittest.TestCase):
    """このテストファイルが動いても、リポジトリ本体の workflow/kit/ は 1 バイトも変わらない（完了条件）"""

    def test_the_real_kit_is_unchanged_after_running_every_block_test(self):
        kit = REPO / "workflow" / "kit"
        digest = lambda: {str(p.relative_to(kit)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(kit.rglob("*")) if p.is_file()}
        before = digest()
        r = subprocess.run([sys.executable, "-m", "unittest",
                            "test_config_block.SaveTest", "test_config_block.BlockRefusalTest"],
                           cwd=str(pathlib.Path(__file__).resolve().parent), text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertEqual(digest(), before, "kit の実物が書き換わっている（一時コピーに対して回すこと）")


@unittest.skipUnless(shutil.which("node"), "node が無い")
class BlockRenderTest(BlockTestCase):
    """編集の欄と画面の中の同期（app.js の部品を node で直に動かす）"""

    def js(self, probe):
        import json as _json
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        from test_console import js_line, js_block
        static = REPO / "console" / "static"
        app = (static / "app.js").read_text(encoding="utf-8")
        w = core.workflow_detail("feature")
        src = self.tmp / "block.js"
        src.write_text("\n".join([
            (static / "strings.js").read_text(encoding="utf-8"),
            *[js_line(app, n) for n in ("esc", "tt", "pad", "fmtT", "tzLabel", "cfgYamlInd", "cfgYamlGetKey",
                                        "cfgYamlSetKey", "cfgVal", "cfgStepDiffRows", "cfgAffectedRows")],
            *[js_block(app, n) for n in ("cfgModelPreview", "cfgBlockPreview", "cfgStepYaml")],
            f"const wf = {_json.dumps(w, ensure_ascii=False)};",
            "const steps = {}; wf.steps.forEach(s => steps[s.id] = s);",
            "const out = {};",
            *[f"out[{k!r}] = {v};" for k, v in probe.items()],
            "console.log(JSON.stringify(out));"]), encoding="utf-8")
        r = subprocess.run(["node", str(src)], text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return _json.loads(r.stdout)

    def test_the_panel_shows_the_block_of_this_step_only(self):
        html = self.js({"y": "cfgStepYaml(wf, steps.design)"})["y"]
        self.assertIn('id="cs-yaml"', html)
        self.assertIn('data-act="config-block" data-wf="feature" data-step="design"', html)
        self.assertIn("# 計画は Fable", html)                              # 行末コメントごと原文が出る
        self.assertNotIn("name: feature", html)                            # ファイル全体は出さない
        self.assertNotIn("- id: implement", html)
        self.assertIn(self.T()["config"]["stepYaml"], html)
        self.assertIn(self.T()["help"]["configStepYamlFixed"], html)       # id と担い手は変えられないと出す

    def test_the_panel_appears_on_a_code_step_too(self):
        """分岐や上限は機械の工程にもあるので、yml の欄は code 工程にも出す（モデルの欄は今までどおり出さない）"""
        html = self.js({"y": "cfgStepYaml(wf, steps.gates)"})["y"]
        self.assertIn('data-step="gates"', html)
        self.assertIn("code: gates.sh", html)

    def test_a_step_whose_block_cannot_be_read_shows_no_panel(self):
        self.assertEqual(self.js({"y": "cfgStepYaml(wf, { id: 'x', yaml_block: null })"})["y"], "")

    def test_reading_a_key_out_of_the_block(self):
        got = self.js({
            "model": "cfgYamlGetKey(steps.design.yaml_block, 'model')",
            "cls": "cfgYamlGetKey(steps.design.yaml_block, 'model_class')",
            "quoted": "cfgYamlGetKey('  - id: x\\n    model: \"claude-opus-5\"   # あとで戻す\\n', 'model')",
            "nested": "cfgYamlGetKey('  - id: x\\n    brief: |\\n      model: claude-opus-5\\n', 'model')",
        })
        self.assertEqual(got["model"], "claude-fable-5-1")                 # 行末コメントを剥がして読む
        self.assertIsNone(got["cls"])                                      # 無い行は null（継承のまま）
        self.assertEqual(got["quoted"], "claude-opus-5")                   # 引用符も剥がす
        self.assertIsNone(got["nested"])                                   # brief の中の同じ語は拾わない

    def test_writing_a_key_back_into_the_block(self):
        got = self.js({
            "same": "cfgYamlSetKey(steps.design.yaml_block, 'model', 'claude-opus-5')",
            "added": "cfgYamlSetKey(steps.review.yaml_block, 'model_class', 'research')",
            "dropped": "cfgYamlSetKey(steps.design.yaml_block, 'model', '')",
        })
        self.assertIn("    model: claude-opus-5\n", got["same"])
        self.assertNotIn("claude-fable-5-1", got["same"])
        self.assertEqual(got["same"].count("    model:"), 1)
        self.assertIn("role: planner", got["same"])                        # ほかの行はそのまま
        self.assertTrue(got["added"].splitlines()[1] == "    model_class: research")   # 無ければ id の直下に足す
        self.assertIn("role: reviewer", got["added"])
        self.assertNotIn("model:", got["dropped"])                         # 空にすれば行を消す（継承に戻す）
        self.assertIn("role: planner", got["dropped"])

    def test_the_preview_lists_the_keys_that_change(self):
        p = self.apply(target="block", workflow="feature", step="design",
                       text=self.block("design").replace("    role: planner\n", "    role: planner\n    timeout_min: 45\n"))
        html = self.js({"pre": f"cfgBlockPreview({json.dumps(p, ensure_ascii=False)})"})["pre"]
        self.assertIn(">timeout_min<", html)
        self.assertIn(">45<", html)
        self.assertIn(self.T()["config"]["stepDiff"], html)
        self.assertIn("commit しません", html)                             # 未コミットの注意は 1 キーのときと同じ
        self.assertIn(self.T()["dialog"]["stepYaml"]["scope"], html)

    def T(self):
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        return __import__("test_strings").load()


if __name__ == "__main__":
    unittest.main()
