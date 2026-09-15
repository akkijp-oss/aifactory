"""配車（dispatch）も先行条件（depends_on）を見る（チケット 573 / ADR-0078）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。偽 `sandbox` の take を失敗させて `kb run` の先へ行かせない（test_kb_resume_paused.py と同じ流儀）。
「どの票を選んだか / 何を理由に飛ばしたか」は dispatch の標準出力・logs/dispatch.log・偽 sandbox の呼ばれ方だけで確かめられる。

- 未完了の先行票を持つ票は dispatch の対象から外れ、次の票が回る
- 飛ばしたときは理由（票 id と 先行票 id=status）が標準出力と dispatch.log の両方に出る（黙って外さない）
- 先行票が done なら今までどおり回る。DB に無い先行票は「未起票」＝未完了に倒す
- `depends_on` を持たない票の挙動は変わらない
- 判定は core の 1 か所にしかない（dispatch 側に規則の写しが無いことを構文木で検査する。#578）
"""
import ast
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
KB = REPO / "kanban" / "bin" / "kb"
DISPATCH = REPO / "glue" / "bin" / "dispatch"
TICKET = "# バグ: 先行条件の確認\n\n偽の sandbox で take を失敗させる。\n"

FAKE = r"""#!/usr/bin/env bash
echo "$@" >> "$CALLS"
case "$1" in
  take) echo "[error] pj=$2 に空きなし" >&2; exit 1 ;;
esac
exit 0
"""


# --- 「規則の写しが無い」の機械検査（#578） -------------------------------------------------
# 守りたいのは **先行条件の判定規則が dispatch に無いこと** であって、`re` や `done` という字面が
# 無いことではない。字面の禁止は、先行条件と無関係に `re` / `"done"` を使いたくなった将来の人を
# 誤検知で止め、「なぜ落ちるか分からない検査」として最後は検査ごと捨てられる（#573 の申し送り）。
# そこで構文木を読み、**depends_on の値を dispatch 自身で判定している**箇所だけを名指しで拾う。
ANCHOR = "        unmet = core.pm_unmet_deps(t)"          # 退行注入の差し込み位置（main() の判定の直前）

# depends_on の値がここへ流れ込んでいたら「判定している」＝規則の写し。値をそのまま行の dict に
# 載せる / core.pm_unmet_deps へ渡すだけなら素通し（判定していない）。
def _judgement(parent, child):
    if isinstance(parent, ast.Compare): return "比較"
    if isinstance(parent, ast.BoolOp): return "論理式"
    if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not): return "否定"
    if isinstance(parent, (ast.If, ast.IfExp, ast.While)) and child is parent.test: return "分岐"
    if isinstance(parent, ast.For) and child is parent.iter: return "繰り返し"
    if isinstance(parent, ast.comprehension) and (child is parent.iter or child in parent.ifs): return "comprehension"
    if isinstance(parent, ast.Call):
        if isinstance(parent.func, ast.Attribute) and child is parent.func: return f"メソッド .{parent.func.attr}()"
        if isinstance(parent.func, ast.Name) and parent.func.id in ("any", "all", "len", "sorted", "filter", "map", "set"):
            return f"{parent.func.id}()"
    return None


def copied_rules(src):
    """先行条件の規則が dispatch に写っている箇所を（行番号と関数名つきで）返す。空なら写しは無い。"""
    tree = ast.parse(src)
    parent, where = {}, {}
    for node in ast.walk(tree):                            # walk は親が先。親の関数名を子へ配る
        here = f"{node.name}()" if isinstance(node, ast.FunctionDef) else where.get(node, "モジュール直下")
        for child in ast.iter_child_nodes(node):
            parent[child], where[child] = node, here
    found = []
    for node in ast.walk(tree):
        if not ((isinstance(node, ast.Constant) and node.value == "depends_on")
                or (isinstance(node, ast.Attribute) and node.attr == "depends_on")
                or (isinstance(node, ast.Name) and node.id == "depends_on")):
            continue
        child, p = node, parent.get(node)
        while p is not None:                               # 値の行き先を上へ辿る。判定に当たったら写し
            why = _judgement(p, child)
            if why:
                found.append(f"行 {node.lineno}: depends_on の値を {where.get(node)} の {why} で"
                             "判定している（規則の正本は core.pm_unmet_deps）")
                break
            child, p = p, parent.get(p)
    return found


def core_verdict_calls(src):
    """dispatch が判定を core へ委ねている箇所（core.pm_unmet_deps の呼び出し）の行番号"""
    return [n.lineno for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "pm_unmet_deps"
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "core"]


def inject(lines):
    """dispatch の main() の判定の直前に数行を差し込んだソースと、差し込んだ先頭の行番号（退行注入用）"""
    src = DISPATCH.read_text(encoding="utf-8").splitlines()
    if ANCHOR not in src:
        raise AssertionError(f"dispatch に差し込み位置（{ANCHOR.strip()}）が無い。退行注入の土台を直すこと")
    i = src.index(ANCHOR)
    return "\n".join(src[:i] + lines + src[i:]) + "\n", i + 1


COPIED_RULE = [                                            # 先行条件の規則を dispatch へ写した数行
    '        deps = [d.strip() for d in (t.get("depends_on") or "").split(",") if d.strip()]',
    '        if any(status_of(d) != "done" for d in deps): skip.add(tid); continue',
]
UNRELATED = [                                              # 先行条件と無関係に re / "done" を使う数行
    "        import re",
    '        slug = re.sub(r"[^a-z0-9]+", "-", t["title"].lower())',
    '        if status_of(tid) == "done": log(f"{tid} {pj} {slug}: もう done"); skip.add(tid); continue',
]


class DispatchDependsOnTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.bin = self.ws / "bin"; self.bin.mkdir()
        fake = self.bin / "sandbox"; fake.write_text(FAKE); fake.chmod(0o755)
        (self.ws / "sandbox-state.json").write_text("{}")
        self.calls = self.ws / "calls.log"
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        AIFACTORY_WORKSPACE=str(self.ws), SANDBOX_STATE=str(self.ws / "sandbox-state.json"),
                        CALLS=str(self.calls))

    def kb(self, *args, input_text=None):
        return subprocess.run([sys.executable, str(KB), *args], input=input_text, text=True, capture_output=True, env=self.env)

    def new(self, tid, *extra, title="先行条件の確認"):
        r = self.kb("new", "kumitate", "bug", title, "--body", "-", "--id", str(tid), *extra, input_text=TICKET)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def dispatch(self, *args):
        r = subprocess.run([sys.executable, str(DISPATCH), *args], text=True, capture_output=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def log_text(self):
        f = self.ws / "logs" / "dispatch.log"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def calls_text(self):
        return self.calls.read_text(encoding="utf-8") if self.calls.exists() else ""

    def test_a_ticket_with_an_unmet_prerequisite_is_skipped_and_the_next_one_runs(self):
        self.new(940, "--depends", "941")
        self.new(941)
        out = self.dispatch("--once").stdout
        self.assertIn("940", out); self.assertIn("先行票 941", out); self.assertIn("飛ばす", out)
        self.assertIn("start 941", out)                        # 依存の無い次の票がその回で回る
        # 理由は標準出力だけでなく記録にも残る（黙って対象外にしない）
        self.assertRegex(self.log_text(), r"940 kumitate: 先行票 941=todo が未完了 → 飛ばす")
        # runner に渡ったのは 941 だけ。940 は VM を取りに行っていない
        self.assertIn("take kumitate 941", self.calls_text())
        self.assertNotIn("940", self.calls_text())

    def test_a_done_prerequisite_lets_the_ticket_run_as_before(self):
        self.new(939)
        self.new(940, "--depends", "939")
        self.assertEqual(self.kb("done", "939").returncode, 0)
        out = self.dispatch("--once").stdout
        self.assertIn("start 940", out)
        self.assertNotIn("先行票", out)
        self.assertIn("take kumitate 940", self.calls_text())

    def test_a_prerequisite_missing_from_the_board_counts_as_unmet(self):
        self.new(940, "--depends", "999")
        out = self.dispatch("--once").stdout
        self.assertIn("先行票 999=未起票 が未完了 → 飛ばす", out)
        self.assertNotIn("start 940", out)

    def test_when_every_todo_waits_on_a_prerequisite_the_runner_is_never_called(self):
        self.new(940, "--depends", "999")
        self.new(942, "--depends", "999")
        out = self.dispatch("--once").stdout
        self.assertIn("todo が無い（または全部飛ばした）。終了", out)
        self.assertEqual(out.count("が未完了 → 飛ばす"), 2)      # 2 件とも理由が出る
        self.assertFalse(self.calls.exists(), self.calls_text())

    def test_a_ticket_without_depends_on_behaves_exactly_as_before(self):
        self.new(940)
        out = self.dispatch("--once").stdout
        self.assertIn("start 940", out)
        self.assertNotIn("先行票", out)
        self.assertIn("take kumitate 940", self.calls_text())

    def test_the_rule_is_not_copied_into_dispatch(self):
        """完了条件「判定が core の 1 か所だけにある」の機械検査（ADR-0015 / ADR-0078 / #578）。

        ★守りたいのは**規則の不在**であって、特定の識別子（`re` や `"done"`）の不在ではない。
          落ちるのは depends_on の値を dispatch 自身で判定したときだけ。`kb show` の行から値を
          拾って core へ渡すだけの next_ticket() は通る。
        """
        self.assertEqual(copied_rules(DISPATCH.read_text(encoding="utf-8")), [])

    def test_dispatch_asks_core_for_the_verdict(self):
        """禁止側だけでなく「core に判定を訊いている」ことも積極的に見る（規則が消えただけでも落ちる）"""
        self.assertTrue(core_verdict_calls(DISPATCH.read_text(encoding="utf-8")),
                        "dispatch が core.pm_unmet_deps を呼んでいない")

    def test_copying_the_rule_into_dispatch_is_caught_by_name(self):
        """退行注入（その 1）: 規則を写すと、行番号と関数名つきで落ちる"""
        src, line = inject(COPIED_RULE)
        found = copied_rules(src)
        self.assertTrue(found, "規則を写したのに検査が落ちない")
        self.assertIn(f"行 {line}:", found[0])
        self.assertIn("depends_on", found[0]); self.assertIn("main()", found[0])
        self.assertIn("core.pm_unmet_deps", found[0])          # どこが正本かを名指しで示す

    def test_an_unrelated_use_of_re_or_done_is_not_flagged(self):
        """退行注入（その 2）: 先行条件と無関係に re / "done" を使っても落ちない（#578 が消した誤検知）"""
        src, _ = inject(UNRELATED)
        for literal in ("import re", "re.sub", '"done"'):
            self.assertIn(literal, src)                        # 旧検査なら確実に落ちていた字面
        self.assertEqual(copied_rules(src), [])
        self.assertTrue(core_verdict_calls(src))


if __name__ == "__main__":
    unittest.main()
