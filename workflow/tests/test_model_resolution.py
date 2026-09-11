"""実効モデルと分岐の解決規則は lib/aifactory_workflow.py が正本で、runner はそれに委ねている（チケット 415 / ADR-0062）。

  python3 -m unittest discover -s workflow/tests -p 'test_model_resolution.py' -v

設定画面が同じ答えを出すために式を写すと、写した分だけ実態と食い違う。ここでは
`Run.model_for` / `Run.transition` が共有関数と同じ答えを返すことを確かめる（VM も claude も使わない）。
定義（工程・経路・期待値）は model_fixture.py に置き、console 側のテスト（console/tests/test_config_save.py）も
同じ表を読む（チケット 416）。
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

_fx = importlib.util.spec_from_file_location("model_fixture", pathlib.Path(__file__).resolve().parent / "model_fixture.py")
fx = importlib.util.module_from_spec(_fx); _fx.loader.exec_module(fx)

ROUTES = fx.ROUTES
STEPS = fx.STEPS


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

    def test_the_order_is_env_then_step_model_then_step_class_then_the_class_row_then_the_default(self):
        """共有 fixture の全組合せで、runner の答えが期待表と一致する（環境変数 > 工程の model > 経路 > 既定）"""
        for sid, env, routes, want in fx.CASES:
            os.environ.pop("CLAUDE_MODEL", None)
            if env: os.environ["CLAUDE_MODEL"] = env
            got = fake_run(fx.routes_of(routes)).model_for(fx.step(sid))
            self.assertEqual(got, (want["model"], want["model_class"]), f"{sid} env={env}")
            self.assertEqual(wf.resolve_model(fx.step(sid), fx.routes_of(routes), env or None),
                             {"role": fx.step(sid)["role"], **want,
                              "route_key": f"MODEL_{want['model_class']}"}, f"{sid} env={env}")

    def test_pinning_one_step_does_not_move_the_other_steps(self):
        """1 工程に model を足しても、同じクラスの他の工程の実効モデルは変わらない"""
        before = {s["id"]: wf.resolve_model(s, ROUTES) for s in STEPS if s.get("role")}
        pinned = [dict(s, **({"model": "claude-haiku-4-5-20251001"} if s["id"] == "design" else {})) for s in STEPS if s.get("role")]
        after = {s["id"]: wf.resolve_model(s, ROUTES) for s in pinned}
        changed = [k for k in before if before[k]["model"] != after[k]["model"]]
        self.assertEqual(changed, ["design"])

    def test_changing_one_route_row_moves_every_step_of_that_class(self):
        """共通経路（MODEL_<クラス>）を変えると、そのクラスの工程は全部動く（共通設定であること）"""
        after_routes = {**ROUTES, "MODEL_judgment": "claude-opus-5"}
        moved = sorted(s["id"] for s in STEPS if s.get("role")
                       and wf.resolve_model(s, ROUTES)["model"] != wf.resolve_model(s, after_routes)["model"])
        self.assertEqual(moved, ["design", "last", "review"])     # pinned は model があるので動かない

    def test_the_key_family_comes_from_the_shared_function(self):
        """鍵の系統（runner が VM の鍵を選ぶ規則）も lib が正本で、runner は委譲しているだけ"""
        for model, fam in fx.FAMILIES:
            self.assertEqual(run.Run.token_family(model), fam, model)
            self.assertEqual(wf.token_family(model), fam, model)

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
        gates = fx.step("gates")
        self.assertEqual(transition(r, gates, False), "implement")
        self.assertEqual(transition(r, gates, False), "implement")
        self.assertEqual(r.state["loops"], {"gates->implement": 2})
        self.assertEqual(transition(r, gates, False), "human")          # 上限を超えたら else
        self.assertEqual(r.state["loops"], {"gates->implement": 2})

    def test_the_severity_bonus_still_adds_one_loop(self):
        """ADR-0053 の加点は runner 側に残っている（共有関数は状態を持たない）"""
        r = fake_run(); r.last_severity = "minor"
        review = fx.step("review")
        self.assertEqual(transition(r, review, False), "implement")     # max_loops 1 の 1 回目
        self.assertEqual(transition(r, review, False), "implement")     # 軽微なので 1 回だけ増える
        self.assertEqual(transition(r, review, False), "human")
        self.assertEqual(wf.transition_of(review, False)["max_loops"], 1)   # 定義の回数そのものは変わらない


if __name__ == "__main__":
    unittest.main()
