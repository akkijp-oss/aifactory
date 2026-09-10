"""実効モデルと分岐の解決規則は lib/aifactory_workflow.py が正本で、runner はそれに委ねている（チケット 415 / ADR-0062）。

  python3 -m unittest discover -s workflow/tests -p 'test_model_resolution.py' -v

設定画面が同じ答えを出すために式を写すと、写した分だけ実態と食い違う。ここでは
`Run.model_for` / `Run.transition` が共有関数と同じ答えを返すことを確かめる（VM も claude も使わない）。
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_loader("model_resolution_run", importlib.machinery.SourceFileLoader("model_resolution_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

import sys
sys.path.insert(0, str(REPO / "lib"))
import aifactory_workflow as wf   # noqa: E402

ROUTES = {"MODEL_judgment": "claude-fable-5-1", "MODEL_research": "claude-sonnet-5",
          "MODEL_coding": "claude-opus-5", "MODEL_default": "claude-opus-5"}

STEPS = [
    {"id": "research", "role": "researcher", "next": "design"},
    {"id": "design", "role": "planner", "next": "implement"},
    {"id": "implement", "role": "implementer", "next": "gates"},
    {"id": "review", "role": "reviewer", "on_pass": "sync", "on_fail": {"goto": "implement", "max_loops": 1, "else": "human"}},
    {"id": "slow", "role": "researcher", "model_class": "coding", "next": "end"},
    {"id": "gates", "code": "gates.sh", "on_pass": "review", "on_fail": {"goto": "implement", "max_loops": 2, "else": "human"}},
    {"id": "pr", "code": "pr-create.sh", "next": "automerge"},
    {"id": "automerge", "code": "pr-automerge.sh", "on_pass": "end", "on_fail": "human"},
    {"id": "last", "role": "planner"},
]


def fake_run(routes=None):
    r = run.Run.__new__(run.Run)
    r.pj, r.task = "kumitate", "415"          # log() が読む（VM も workflow の読み込みも要らない）
    r.routes = dict(ROUTES if routes is None else routes)
    r.last_severity = None
    r.state = {"loops": {}, "severity_bonus": {}}
    return r


def transition(r, step, ok):
    with contextlib.redirect_stdout(io.StringIO()):
        return r.transition(step, ok)


class ModelForTest(unittest.TestCase):
    def setUp(self):
        self.env = os.environ.pop("CLAUDE_MODEL", None)

    def tearDown(self):
        if self.env is not None: os.environ["CLAUDE_MODEL"] = self.env
        else: os.environ.pop("CLAUDE_MODEL", None)

    def test_the_runner_and_the_shared_function_agree(self):
        r = fake_run()
        for st in STEPS:
            if "role" not in st: continue
            want = wf.resolve_model(st, ROUTES)
            self.assertEqual(r.model_for(st), (want["model"], want["model_class"]), st["id"])

    def test_the_order_is_env_then_step_class_then_the_class_row_then_the_default(self):
        r = fake_run()
        self.assertEqual(r.model_for({"id": "d", "role": "planner"}), ("claude-fable-5-1", "judgment"))
        self.assertEqual(r.model_for({"id": "s", "role": "researcher", "model_class": "coding"}), ("claude-opus-5", "coding"))
        self.assertEqual(fake_run({"MODEL_default": "only"}).model_for({"id": "d", "role": "planner"}), ("only", "judgment"))
        os.environ["CLAUDE_MODEL"] = "claude-from-env"
        self.assertEqual(r.model_for({"id": "d", "role": "planner"}), ("claude-from-env", "judgment"))

    def test_a_code_step_or_an_unknown_role_still_raises_like_before(self):
        r = fake_run()
        with self.assertRaises(KeyError): r.model_for({"id": "gates", "code": "gates.sh"})
        with self.assertRaises(KeyError): r.model_for({"id": "x", "role": "wizard"})
        with self.assertRaises(KeyError): fake_run({}).model_for({"id": "d", "role": "planner"})

    def test_the_key_families_of_a_workflow_still_come_from_the_step_models(self):
        """needed_keys() は model_for() を通る（委譲で壊れていないこと）"""
        r = fake_run(); r.wf = {"steps": STEPS}
        self.assertEqual(r.needed_keys(), ["fable", "other"])


class TransitionTest(unittest.TestCase):
    def test_where_each_step_goes_agrees_with_the_shared_function(self):
        for st in STEPS:
            for ok in (True, False):
                want = wf.transition_of(st, ok)
                got = transition(fake_run(), st, ok)
                self.assertEqual(got, want["to"], f"{st['id']} ok={ok}")

    def test_the_loop_count_and_the_limit_stay_in_the_runner(self):
        """行き先だけを共有関数に委ね、回数の数え上げ（state.json の loops）は runner が持つ"""
        r = fake_run()
        gates = STEPS[5]
        self.assertEqual(transition(r, gates, False), "implement")
        self.assertEqual(transition(r, gates, False), "implement")
        self.assertEqual(r.state["loops"], {"gates->implement": 2})
        self.assertEqual(transition(r, gates, False), "human")          # 上限を超えたら else
        self.assertEqual(r.state["loops"], {"gates->implement": 2})

    def test_the_severity_bonus_still_adds_one_loop(self):
        """ADR-0053 の加点は runner 側に残っている（共有関数は状態を持たない）"""
        r = fake_run(); r.last_severity = "minor"
        review = STEPS[3]
        self.assertEqual(transition(r, review, False), "implement")     # max_loops 1 の 1 回目
        self.assertEqual(transition(r, review, False), "implement")     # 軽微なので 1 回だけ増える
        self.assertEqual(transition(r, review, False), "human")
        self.assertEqual(wf.transition_of(review, False)["max_loops"], 1)   # 定義の回数そのものは変わらない


if __name__ == "__main__":
    unittest.main()
