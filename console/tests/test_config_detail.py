"""設定画面の workflow 詳細（チケット 415）。/api/config が定義から解いた工程の詳細を返すこと。

  python3 -m unittest discover -s console/tests -p 'test_config_detail.py' -v

- 同梱の kit の定義（feature / feature-long / research）で、分岐・上限・実効モデルが定義と一致すること
- 合成 fixture（値未指定・未知のキー・enum 外の役割・読めない yml）でも endpoint が落ちないこと
- console の環境変数 CLAUDE_MODEL は読まない（run を起こすプロセスの値なので、ここで読むと嘘になる）
- 読み取りだけ: /api/config を叩いてもジョブは増えない
- 既存の形（steps[].id / role / code・routes・roles）は残す（MCP の config も同じ形を使う）
"""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
CONSOLE = REPO / "console" / "bin" / "console"
WORKFLOWS = REPO / "workflow" / "kit" / "workflows"

sys.path.insert(0, str(REPO / "console" / "lib"))
sys.path.insert(0, str(REPO / "lib"))
import core  # noqa: E402
import aifactory_workflow as wfdef  # noqa: E402


def load(name):
    import yaml
    return yaml.safe_load((WORKFLOWS / f"{name}.yml").read_text(encoding="utf-8"))


def step_of(wf, sid):
    return next(s for s in wf["steps"] if s["id"] == sid)


def trans(step, when):
    return next(t for t in step["transitions"] if t["when"] == when)


class SharedRulesTest(unittest.TestCase):
    """lib/aifactory_workflow.py の純関数（runner と console が共有する規則）"""

    ROUTES = {"MODEL_judgment": "j", "MODEL_research": "r", "MODEL_coding": "c", "MODEL_default": "d"}

    def test_the_role_decides_the_class_and_the_step_can_override_it(self):
        r = wfdef.resolve_model({"id": "x", "role": "implementer"}, self.ROUTES)
        self.assertEqual((r["model_class"], r["class_from"], r["model"], r["model_from"]), ("coding", "role", "c", "routes"))
        r = wfdef.resolve_model({"id": "x", "role": "implementer", "model_class": "judgment"}, self.ROUTES)
        self.assertEqual((r["model_class"], r["class_from"], r["model"], r["model_from"]), ("judgment", "step", "j", "routes"))

    def test_the_env_override_wins_and_a_missing_class_row_falls_back_to_the_default(self):
        r = wfdef.resolve_model({"id": "x", "role": "planner"}, self.ROUTES, "claude-from-env")
        self.assertEqual((r["model"], r["model_from"]), ("claude-from-env", "env"))
        r = wfdef.resolve_model({"id": "x", "role": "planner"}, {"MODEL_default": "d"})
        self.assertEqual((r["model"], r["model_from"], r["route_key"]), ("d", "default", "MODEL_judgment"))

    def test_a_code_step_has_no_model_and_an_unknown_role_says_so_instead_of_raising(self):
        self.assertIsNone(wfdef.resolve_model({"id": "gates", "code": "gates.sh"}, self.ROUTES))
        r = wfdef.resolve_model({"id": "x", "role": "wizard"}, self.ROUTES)
        self.assertEqual((r["model_class"], r["class_from"], r["model"], r["model_from"]), (None, None, None, None))
        r = wfdef.resolve_model({"id": "x", "role": "planner"}, {})
        self.assertEqual((r["model_class"], r["model"], r["model_from"]), ("judgment", None, None))

    def test_where_a_step_goes_follows_the_runners_rules(self):
        step = {"id": "gates", "code": "gates.sh", "on_pass": "review", "on_fail": {"goto": "implement", "max_loops": 2}}
        self.assertEqual(wfdef.transition_of(step, True), {"to": "review", "kind": "step", "max_loops": None, "else": None, "else_kind": None, "source": "on_pass"})
        f = wfdef.transition_of(step, False)
        self.assertEqual((f["to"], f["max_loops"], f["else"], f["else_kind"], f["source"]), ("implement", 2, "human", "human", "on_fail"))
        # 合否の分岐が無い step: 成功は next、失敗は人間待ち（schema の説明にある「結果に関係なく次へ」ではない）
        nxt = {"id": "pr", "code": "pr-create.sh", "next": "automerge"}
        self.assertEqual(wfdef.transition_of(nxt, True)["to"], "automerge")
        self.assertEqual((wfdef.transition_of(nxt, False)["to"], wfdef.transition_of(nxt, False)["source"]), ("human", "default"))
        # next も on_* も無い step: 成功は正常終了、失敗は人間待ち
        bare = {"id": "last", "role": "planner"}
        self.assertEqual((wfdef.transition_of(bare, True)["to"], wfdef.transition_of(bare, True)["kind"]), ("end", "end"))
        self.assertEqual(wfdef.transition_of(bare, False)["to"], "human")

    def test_the_main_path_stops_at_the_end_and_does_not_loop_forever(self):
        steps = [{"id": "a", "next": "b"}, {"id": "b", "on_pass": "end", "on_fail": {"goto": "a"}}]
        self.assertEqual(wfdef.main_path(steps), ["a", "b"])
        self.assertEqual(wfdef.main_path([{"id": "a", "next": "b"}, {"id": "b", "next": "a"}]), ["a", "b"])   # 巡回でも止まる
        self.assertEqual(wfdef.main_path([{"id": "a", "next": "nowhere"}]), ["a"])                            # 未知の行き先で止まる
        self.assertEqual(wfdef.main_path([]), [])


class WorkflowDetailTest(unittest.TestCase):
    """同梱の kit の定義から解いた値が yml と一致すること（画面に手書きで写さない）"""

    def test_feature_branches_match_the_definition(self):
        w = core.workflow_detail("feature")
        self.assertIsNone(w["parse_error"]); self.assertEqual(w["errors"], [])
        self.assertEqual(trans(step_of(w, "sync"), "pass")["to"], "pr")                 # 取り込めたら resolve を飛ばして PR へ
        self.assertEqual(trans(step_of(w, "sync"), "fail")["to"], "resolve")
        self.assertEqual(trans(step_of(w, "sync"), "fail")["max_loops"], 2)
        self.assertEqual(trans(step_of(w, "sync"), "fail")["else"], "human")
        self.assertEqual(trans(step_of(w, "gates"), "fail")["to"], "implement")
        self.assertEqual(trans(step_of(w, "gates"), "fail")["max_loops"], 2)
        self.assertEqual(trans(step_of(w, "review"), "fail")["to"], "implement")
        self.assertEqual(trans(step_of(w, "review"), "fail")["max_loops"], 1)
        self.assertEqual(trans(step_of(w, "automerge"), "fail")["to"], "human")
        self.assertEqual(trans(step_of(w, "automerge"), "pass")["kind"], "end")

    def test_resolve_is_not_on_the_success_path(self):
        """resolve は sync が失敗したときだけ回る。一直線に並べると常に回るように読める（チケット 415 の症状）"""
        w = core.workflow_detail("feature")
        self.assertEqual(w["main_path"], ["research", "design", "implement", "gates", "review", "sync", "pr", "automerge"])
        self.assertNotIn("resolve", w["main_path"])
        self.assertIn("resolve", [s["id"] for s in w["steps"]])

    def test_the_time_limit_says_whether_it_is_the_schema_default(self):
        f, l = core.workflow_detail("feature"), core.workflow_detail("feature-long")
        self.assertEqual((step_of(f, "implement")["timeout_min"], step_of(f, "implement")["timeout_default"]), (60, True))
        self.assertEqual((step_of(l, "implement")["timeout_min"], step_of(l, "implement")["timeout_default"]), (180, False))
        self.assertEqual((step_of(l, "research")["timeout_min"], step_of(l, "research")["timeout_default"]), (40, False))
        self.assertEqual(step_of(f, "implement")["timeout_min"], load("feature").get("steps")[2].get("timeout_min", 60))

    def test_code_steps_have_no_model_and_agent_steps_resolve_through_the_routes(self):
        w = core.workflow_detail("feature")
        for sid in ("gates", "sync", "pr", "automerge"):
            self.assertIsNone(step_of(w, sid)["model_resolved"], sid)
        d = step_of(w, "design")["model_resolved"]
        self.assertEqual((d["model_class"], d["class_from"], d["route_key"], d["model_from"]), ("judgment", "role", "MODEL_judgment", "routes"))
        self.assertEqual(d["model"], core.model_routes()["MODEL_judgment"])
        i = step_of(w, "implement")["model_resolved"]
        self.assertEqual((i["model_class"], i["model"]), ("coding", core.model_routes()["MODEL_coding"]))

    def test_a_step_that_overrides_the_class_wins_over_the_role_default(self):
        st = core.step_detail({"id": "x", "role": "implementer", "model_class": "research"}, core.model_routes(), *core.step_props())
        self.assertEqual(st["model_resolved"]["model_class"], "research")
        self.assertEqual(st["model_resolved"]["class_from"], "step")
        self.assertEqual(st["model_resolved"]["model"], core.model_routes()["MODEL_research"])

    def test_research_has_no_code_step_and_ends_normally(self):
        w = core.workflow_detail("research")
        self.assertEqual(w["main_path"], ["research", "judge"])
        self.assertTrue(all(s["role"] for s in w["steps"]))
        self.assertEqual(trans(step_of(w, "judge"), "pass")["kind"], "end")


class SyntheticFixtureTest(unittest.TestCase):
    """未指定値・未知のキー・enum 外の役割・読めない yml を置いても、隠さず説明して残りを返すこと"""

    BROKEN = "name: broken\nsteps:\n  - id: a\n   role: planner\n"      # 字下げが揃っていない（YAML として読めない）
    ODD = ("name: odd\ndescription: 合成 fixture\nsteps:\n"
           "  - id: one\n    role: wizard\n    whatever: 1\n    next: two\n"
           "  - id: two\n    code: none.sh\n")

    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-wf-fixture-"))
        for f in WORKFLOWS.glob("*.yml"): shutil.copy(f, cls.tmp / f.name)
        (cls.tmp / "zz-broken.yml").write_text(cls.BROKEN, encoding="utf-8")
        (cls.tmp / "zz-odd.yml").write_text(cls.ODD, encoding="utf-8")
        cls.orig = core.WORKFLOWS
        core.WORKFLOWS = cls.tmp

    @classmethod
    def tearDownClass(cls):
        core.WORKFLOWS = cls.orig
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_an_unreadable_definition_is_reported_and_does_not_break_the_rest(self):
        names = core.kinds(self.tmp)
        self.assertIn("zz-broken", names); self.assertIn("feature", names)
        b = core.workflow_detail("zz-broken")
        self.assertTrue(b["parse_error"])
        self.assertEqual(b["steps"], [])
        self.assertEqual(core.workflow_detail("feature")["main_path"][0], "research")   # 隣は普通に読める

    def test_unknown_keys_and_an_unknown_role_are_shown_not_hidden(self):
        w = core.workflow_detail("zz-odd")
        one = step_of(w, "one")
        self.assertEqual(one["unknown_keys"], ["whatever"])
        self.assertEqual(one["model_resolved"]["model_class"], None)      # 役割が enum 外なので解決できない
        self.assertEqual(one["model_resolved"]["role"], "wizard")
        self.assertTrue(w["errors"])                                     # schema との食い違いも隠さない
        self.assertTrue(any("wizard" in e["message"] for e in w["errors"]), w["errors"])

    def test_values_not_written_in_the_definition_fall_back_to_the_schema_default(self):
        w = core.workflow_detail("zz-odd")
        self.assertEqual(w["inputs"], []); self.assertIsNone(w["start"]); self.assertIsNone(w["base_branch"])
        two = step_of(w, "two")
        self.assertEqual((two["timeout_min"], two["timeout_default"]), (60, True))
        self.assertEqual(two["inputs"] if "inputs" in two else [], [])
        self.assertEqual(trans(two, "pass")["to"], "end")                # next が無いので正常終了
        self.assertEqual(trans(two, "fail")["to"], "human")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


class ConfigApiTest(unittest.TestCase):
    """/api/config の実物（読み取りだけ・後方互換・console の環境変数を読まない）"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-config-api-"))
        cls.ws = cls.tmp / "ws"; cls.ws.mkdir()
        cls.port = free_port()
        # console 側に CLAUDE_MODEL を置いても API の値は変わらない（run を起こすプロセスの環境変数なので）
        env = {**os.environ, "AIFACTORY_WORKSPACE": str(cls.ws), "CONSOLE_JOBS": str(cls.tmp / "jobs"),
               "CLAUDE_MODEL": "claude-console-must-not-read-this"}
        cls.proc = subprocess.Popen([sys.executable, str(CONSOLE), "--port", str(cls.port)], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.base = f"http://127.0.0.1:{cls.port}"
        for _ in range(50):
            try: cls.get("/api/overview"); break
            except Exception: time.sleep(0.1)
        else: raise RuntimeError("console が起動しない")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(timeout=10)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def get(cls, path):
        with urllib.request.urlopen(cls.base + path, timeout=10) as r: return r.status, json.loads(r.read())

    def wf(self, d, name):
        return next(w for w in d["workflows"] if w["name"] == name)

    def test_the_endpoint_serves_the_step_details(self):
        st, d = self.get("/api/config")
        self.assertEqual(st, 200)
        w = self.wf(d, "feature")
        self.assertEqual(w["path"], "workflow/kit/workflows/feature.yml")   # 原文を画面から開ける
        for k in ("inputs", "outputs", "transitions", "model_resolved", "timeout_min", "timeout_default", "unknown_keys"):
            self.assertIn(k, step_of(w, "implement"), k)
        self.assertIn("30 分ごと", step_of(self.wf(d, "feature-long"), "implement")["brief"])   # brief（依頼文に付く追加指示）も届く
        self.assertTrue(step_of(w, "design")["brief"])
        self.assertEqual(d["role_defaults"], {"planner": "judgment", "reviewer": "judgment", "researcher": "research", "implementer": "coding"})
        self.assertTrue(d["env_override_possible"])
        self.assertEqual(d["timeout_default"], 60)

    def test_the_console_environment_does_not_leak_into_the_resolved_model(self):
        _, d = self.get("/api/config")
        s = step_of(self.wf(d, "feature"), "implement")
        self.assertEqual(s["model_resolved"]["model_from"], "routes")
        self.assertNotEqual(s["model_resolved"]["model"], "claude-console-must-not-read-this")
        self.assertNotIn("claude-console-must-not-read-this", json.dumps(d))

    def test_looking_at_the_definition_does_not_start_anything(self):
        _, before = self.get("/api/jobs")
        self.get("/api/config"); self.get("/api/config")
        _, after = self.get("/api/jobs")
        self.assertEqual(len(after["jobs"]), len(before["jobs"]))

    def test_the_old_shape_is_still_there_for_the_mcp_config_tool(self):
        _, d = self.get("/api/config")
        for k in ("workflows", "routes", "roles", "templates", "kb_root", "repo", "paths", "git"):
            self.assertIn(k, d, k)
        w = self.wf(d, "feature")
        self.assertTrue(w["description"])
        for s in w["steps"]:
            self.assertEqual(set(s) >= {"id", "role", "code"}, True, s)
        self.assertEqual([s["code"] for s in w["steps"] if s["id"] == "gates"], ["gates.sh"])
        self.assertIsNone(step_of(w, "gates")["role"])
        self.assertIn("MODEL_default", d["routes"])
        self.assertIn("implementer", d["roles"])
        self.assertTrue(all(not k.startswith((".", "_")) for k in [x["name"] for x in d["workflows"]]))   # #218 の除外は維持


if __name__ == "__main__":
    unittest.main()
