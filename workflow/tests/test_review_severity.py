"""reviewer が `severity: minor` と書いた FAIL は、戻せる回数を使い切っていても **もう 1 周だけ** implement に戻る（チケット 352 / ADR-0053）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。`Run.__new__` に最小の state だけ持たせて `transition()` を直接回す
（review.md の解析は module 関数 `review_verdict` / `review_severity` を単体で確かめる）。

- minor で上限到達 → goto に戻る。同じ遷移でもう一度 minor → human（加点は 1 遷移につき 1 回きり）
- major / severity 無指定 → 従来どおり human
- reviewer 以外の step は本文に minor と書いてあっても加点しない
- 1 行目の `# レビュー: PASS|FAIL` の契約は変えない（severity は 2 行目以降の 1 行）
"""
import importlib.machinery
import importlib.util
import io
import contextlib
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_loader("review_severity_run", importlib.machinery.SourceFileLoader("review_severity_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

REVIEW_STEP = {"id": "review", "role": "reviewer", "on_pass": "sync",
               "on_fail": {"goto": "implement", "max_loops": 1, "else": "human"}}
GATES_STEP = {"id": "gates", "code": "gates.sh",
              "on_fail": {"goto": "implement", "max_loops": 2, "else": "human"}}


def fake_run(severity=None, loops=None, bonus=None):
    """transition() だけを動かす最小の Run（VM も workflow の読み込みも要らない）"""
    r = run.Run.__new__(run.Run)
    r.pj, r.task = "kumitate", "352"
    r.last_severity = severity
    r.state = {"loops": dict(loops or {}), "severity_bonus": dict(bonus or {})}
    return r


def transition(r, step, ok=False):
    with contextlib.redirect_stdout(io.StringIO()):      # log の 1 行はテストの出力に混ぜない
        return r.transition(step, ok)


class ReviewParseTest(unittest.TestCase):
    def test_the_first_line_decides_pass_or_fail(self):
        self.assertEqual(run.review_verdict("# レビュー: PASS\n## 要約\n良い\n"), "PASS")
        self.assertEqual(run.review_verdict("# レビュー: FAIL\nseverity: minor\n"), "FAIL")
        self.assertIsNone(run.review_verdict(""))
        self.assertIsNone(run.review_verdict(None))

    def test_severity_is_read_from_a_line_below_the_verdict(self):
        self.assertEqual(run.review_severity("# レビュー: FAIL\nseverity: minor\n"), "minor")
        self.assertEqual(run.review_severity("# レビュー: FAIL\n## 要約\n\n  SEVERITY : Major  \n"), "major")
        self.assertIsNone(run.review_severity("# レビュー: FAIL\n## 指摘\n1. 直す\n"))

    def test_the_verdict_line_is_not_mistaken_for_a_severity(self):
        """1 行目は 5 つの workflow が共有する契約。そこに何が書かれていても severity として読まない"""
        self.assertIsNone(run.review_severity("# レビュー: PASS"))
        self.assertIsNone(run.review_severity("severity: minor\n"))       # 1 行目だけの review.md
        self.assertIsNone(run.review_severity("# レビュー: FAIL severity: minor\n"))

    def test_a_word_that_merely_contains_minor_is_not_a_severity(self):
        self.assertIsNone(run.review_severity("# レビュー: FAIL\n指摘は minor な範囲にとどまる\n"))


class SeverityBonusTest(unittest.TestCase):
    def test_minor_gets_one_more_loop_after_the_limit_and_only_one(self):
        r = fake_run(severity="minor", loops={"review->implement": 1})     # max_loops 1 を使い切った状態
        self.assertEqual(transition(r, REVIEW_STEP), "implement")
        self.assertEqual(r.state["loops"]["review->implement"], 2)
        self.assertEqual(r.state["severity_bonus"], {"review->implement": 1})
        # 同じ遷移でもう一度 minor と書かれても増えない（暴走させない）
        self.assertEqual(transition(r, REVIEW_STEP), "human")
        self.assertEqual(r.state["loops"]["review->implement"], 2)
        self.assertEqual(r.state["severity_bonus"], {"review->implement": 1})

    def test_major_or_no_severity_goes_to_human_as_before(self):
        for sev in ("major", None):
            r = fake_run(severity=sev, loops={"review->implement": 1})
            self.assertEqual(transition(r, REVIEW_STEP), "human", sev)
            self.assertEqual(r.state["severity_bonus"], {}, sev)

    def test_the_bonus_is_not_spent_while_the_limit_is_not_reached(self):
        """まだ戻せる回数が残っているうちは加点しない（1 回目の FAIL で使い切らせない）"""
        r = fake_run(severity="minor")
        self.assertEqual(transition(r, REVIEW_STEP), "implement")
        self.assertEqual(r.state["severity_bonus"], {})
        self.assertEqual(transition(r, REVIEW_STEP), "implement")          # ここで加点を使う
        self.assertEqual(r.state["severity_bonus"], {"review->implement": 1})
        self.assertEqual(transition(r, REVIEW_STEP), "human")

    def test_a_step_that_is_not_a_reviewer_never_gets_the_bonus(self):
        """gates の戻しは severity と関係ない（reviewer だけが重さを書ける）"""
        r = fake_run(severity="minor", loops={"gates->implement": 2})
        self.assertEqual(transition(r, GATES_STEP), "human")
        self.assertEqual(r.state["severity_bonus"], {})

    def test_a_passing_review_is_unaffected(self):
        r = fake_run(severity="minor", loops={"review->implement": 1})
        self.assertEqual(transition(r, REVIEW_STEP, ok=True), "sync")
        self.assertEqual(r.state["severity_bonus"], {})

    def test_an_old_state_without_the_field_still_works(self):
        """severity_bonus を持たない古い記録（--resume で読み直した state.json）でも落ちない"""
        r = fake_run(severity="minor", loops={"review->implement": 1})
        del r.state["severity_bonus"]
        self.assertEqual(transition(r, REVIEW_STEP), "implement")
        self.assertEqual(r.state["severity_bonus"], {"review->implement": 1})

    def test_a_run_that_never_reviewed_has_no_severity(self):
        """last_severity が付いていない Run でも transition() は従来どおり動く"""
        r = fake_run()
        del r.last_severity
        self.assertEqual(transition(r, REVIEW_STEP), "implement")
        self.assertEqual(transition(r, REVIEW_STEP), "human")


class RealWorkflowTest(unittest.TestCase):
    """実物の workflow 定義（`kit/workflows/*.yml` の review step）で、戻る回数が max_loops + 1 で止まることを固定する"""

    def review_steps(self):
        """`role: reviewer` を持つ step を全部の workflow から拾う（workflow が増えても取りこぼさない）"""
        for f in sorted((REPO / "workflow/kit/workflows").glob("*.yml")):
            for s in run.load_yaml(f)["steps"]:
                if s.get("role") == "reviewer": yield f.stem, s

    def loops_until_human(self, step, severity):
        r = fake_run(severity=severity)
        n = 0
        while transition(r, step) != "human":
            n += 1
            self.assertLess(n, 10, "戻しが止まらない")
        return n

    def test_minor_adds_exactly_one_loop_to_every_workflow(self):
        seen = []
        for name, step in self.review_steps():
            seen.append(name)
            limit = step["on_fail"]["max_loops"]
            self.assertEqual(self.loops_until_human(step, None), limit, name)
            self.assertEqual(self.loops_until_human(step, "major"), limit, name)
            self.assertEqual(self.loops_until_human(step, "minor"), limit + 1, name)
        # goto 先が implement でない merge-pr（goto: resolve）も含め、review がある workflow は全部見たか
        self.assertEqual(sorted(seen), ["bug", "docs", "feature", "feature-long", "hotfix", "merge-pr"])


if __name__ == "__main__":
    unittest.main()
