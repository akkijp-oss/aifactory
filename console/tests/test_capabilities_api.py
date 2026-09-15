"""console から能力宣言が読め、起票の警告が戻り値に載ること（チケット 552）。

  python3 -m unittest discover -s console/tests -p 'test_capabilities_api.py' -v

VM も claude も使わない。一時 workspace に PJ 定義を置き、`core` を別プロセスで呼ぶ
（test_next_preview.py と同じ流儀）。

- `project_show` の `capabilities` が project.yml の宣言をそのまま返す（完了条件 1 番目の読み口）
- `ticket_new` は実行不能な完了条件でも **起票する**。理由は `warnings[]` に載るだけ
- 判定の規則は console 側に写さない（kb が stderr に出した行を拾うだけ）
"""
import ast
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
CORE = REPO / "console" / "lib" / "core.py"
PJ = "kumitate"

PREAMBLE = '''
import json, sys
sys.path.insert(0, %r)
import core
def out(v): print(json.dumps(v, ensure_ascii=False, default=str))
''' % str(REPO / "console" / "lib")

BODY = "## 完了条件\n- ブラウザで目視して実 iframe を確かめる\n- テストが緑\n"


class CapabilitiesApiTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.env = dict(os.environ, AIFACTORY_WORKSPACE=str(self.ws), CONSOLE_JOBS=str(self.ws / "jobs"))

    def core(self, expr):
        r = subprocess.run([sys.executable, "-c", PREAMBLE + f"out({expr})\n"], env=self.env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def test_project_show_returns_the_declaration(self):
        """examples の宣言がそのまま読める（未宣言のキーは出てこない）"""
        got = self.core("core.project_show(%r)" % PJ)["capabilities"]
        self.assertEqual(got, {"browser": False, "docker": False, "gui": False})
        self.assertEqual(self.core("core.project_show('aifactory')")["capabilities"],
                         {"browser": False, "docker": False, "egress": True, "gui": False})

    def test_project_show_is_none_when_nothing_is_declared(self):
        d = self.ws / "projects" / PJ; d.mkdir(parents=True)
        (d / "project.yml").write_text("name: kumitate\nrepo: x/y\napp_dir: /a\n", encoding="utf-8")
        self.assertIsNone(self.core("core.project_show(%r)" % PJ)["capabilities"])

    def test_ticket_new_creates_the_ticket_and_reports_the_warnings(self):
        r = self.core("core.ticket_new({'pj': %r, 'kind': 'feature', 'title': 'iframe', 'body': %r})" % (PJ, BODY))
        self.assertIsNotNone(r["id"])
        self.assertTrue(list((self.ws / "kanban" / "tickets").glob("*.md")), "票が作られていない")
        self.assertEqual(len(r["warnings"]), 2)        # 当たり 1 行 + 但し書き
        self.assertIn("browser", r["warnings"][0])
        self.assertIn("実 iframe", r["warnings"][0])

    def test_warnings_is_empty_when_nothing_matches(self):
        r = self.core("core.ticket_new({'pj': %r, 'kind': 'feature', 'title': 'x', 'body': '## 完了条件\\n- テストが緑\\n'})" % PJ)
        self.assertEqual(r["warnings"], [])

    def test_core_does_not_judge_for_itself(self):
        """判定は kb（正本は lib/aifactory_capabilities.py）の 1 か所。console に写しがあると必ずずれる。

        字面（「ブラウザ」など）の禁止は、能力照合と無関係な文でも落ちる誤検知になるので採らない（#573 の申し送り）。
        代わりに **core が照合の道具を持ち込んでいないこと** を import と参照名で見る。
        """
        tree = ast.parse(CORE.read_text(encoding="utf-8"))
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import): imported |= {a.name for a in n.names}
            if isinstance(n, ast.ImportFrom): imported.add(n.module or "")
        self.assertNotIn("aifactory_capabilities", imported)
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertFalse({"scan", "format_warnings", "prompt_section"} & names)


if __name__ == "__main__":
    unittest.main()
