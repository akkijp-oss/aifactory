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
        self.assertEqual(d["timeout_min_default"], 60)

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


@unittest.skipUnless(shutil.which("node"), "node が無い")
class ConfigRenderTest(unittest.TestCase):
    """設定の詳細の描画（app.js の 1 行の部品を node で直に動かす）。
    一覧と詳細が本物のリンクを出すこと、code 工程にモデル名を出さないこと"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        from test_console import js_line
        static = REPO / "console" / "static"
        app = (static / "app.js").read_text(encoding="utf-8")
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-config-render-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        w = core.workflow_detail("feature")
        probe = {
            "wfLink": "cfgWfLink('feature')",
            "stepLink": "cfgStepLink('feature', 'implement')",
            "quoted": "cfgStepLink('a b', '<x>')",
            "modelAgent": "cfgModel(steps.implement.model_resolved)",
            "modelCode": "cfgModel(steps.gates.model_resolved)",
            "modelUnknownRole": "cfgModel({role: 'wizard', model_class: null, class_from: null, route_key: null, model: null, model_from: null})",
            "modelStepClass": "cfgModel({role: 'implementer', model_class: 'research', class_from: 'step', route_key: 'MODEL_research', model: 'claude-sonnet-5', model_from: 'routes'})",
            "modelFallback": "cfgModel({role: 'planner', model_class: 'judgment', class_from: 'role', route_key: 'MODEL_judgment', model: 'd', model_from: 'default'})",
            "flowMain": "cfgFlow(wf, wf.main_path)",
            "cond": "JSON.stringify(cfgCond(wf).map(s => s.id))",
            "gatesFail": "cfgTrans(trans(steps.gates, 'fail'))",
            "syncPass": "cfgTrans(trans(steps.sync, 'pass'))",
            "researchFail": "cfgTrans(trans(steps.research, 'fail'))",
            "automergePass": "cfgTrans(trans(steps.automerge, 'pass'))",
            "automergeFail": "cfgTrans(trans(steps.automerge, 'fail'))",
            "files": "cfgFiles(steps.implement)",
            "filesNone": "cfgFiles({})",
            "broken": "cfgBroken({parse_error: '字下げが揃っていません', errors: []})",
            "schemaErr": "cfgBroken({parse_error: null, errors: [{path: '$.steps[0].role', message: 'wizard is not one of ...'}]})",
            "clean": "cfgBroken({parse_error: null, errors: []})",
            "unknown": "cfgUnknown({unknown_keys: ['whatever']})",
            "unknownNone": "cfgUnknown({unknown_keys: []})",
        }
        src = cls.tmp / "cfg.js"
        src.write_text("\n".join([
            (static / "strings.js").read_text(encoding="utf-8"),
            *[js_line(app, n) for n in ("esc", "tt", "cfgWfLink", "cfgStepLink", "cfgFlow", "cfgCond", "cfgTarget", "cfgTrans", "cfgModel", "cfgFiles", "cfgBroken", "cfgUnknown")],
            f"const wf = {json.dumps(w, ensure_ascii=False)};",
            "const steps = {}; wf.steps.forEach(s => steps[s.id] = s);",
            "const trans = (s, when) => s.transitions.find(t => t.when === when);",
            "const out = {};",
            *[f"out[{k!r}] = {v};" for k, v in probe.items()],
            "console.log(JSON.stringify(out));"]), encoding="utf-8")
        r = subprocess.run(["node", str(src)], text=True, capture_output=True)
        assert r.returncode == 0, r.stderr
        cls.html = json.loads(r.stdout)
        cls.T = __import__("test_strings").load()

    def test_the_list_and_the_flow_use_real_links(self):
        self.assertEqual(self.html["wfLink"], '<a href="#/config/workflow/feature">feature</a>')
        self.assertEqual(self.html["stepLink"], '<a href="#/config/workflow/feature/implement">implement</a>')
        self.assertIn("#/config/workflow/a%20b/%3Cx%3E", self.html["quoted"])      # href も本文も逃がしてある
        self.assertIn("&lt;x&gt;", self.html["quoted"])

    def test_the_success_path_does_not_include_the_conditional_step(self):
        """一覧でも詳細でも、resolve を成功の道に混ぜない（混ぜると常に回るように読める）"""
        self.assertNotIn(">resolve<", self.html["flowMain"])
        self.assertIn(">sync<", self.html["flowMain"]); self.assertIn(">pr<", self.html["flowMain"])
        self.assertEqual(json.loads(self.html["cond"]), ["resolve"])

    def test_a_code_step_says_it_uses_no_model_and_shows_no_model_name(self):
        h = self.html["modelCode"]
        self.assertIn(self.T["help"]["configNoModel"], h)
        self.assertNotIn("claude", h)
        self.assertNotIn("MODEL_", h)

    def test_an_agent_step_shows_the_chain_from_the_class_to_the_model(self):
        h = self.html["modelAgent"]
        self.assertIn("coding", h); self.assertIn("MODEL_coding", h); self.assertIn("claude-opus-5", h)
        self.assertIn("implementer", h)                                          # 役割の既定であることが読める
        self.assertIn(self.T["help"]["configEnvOverride"], h)                    # 実行時上書きは「分からない」と書く
        self.assertIn('href="#/stats"', h)                                       # 実績は統計で見る
        self.assertIn(self.T["config"]["classFromStep"], self.html["modelStepClass"])
        self.assertIn("MODEL_research", self.html["modelStepClass"])
        self.assertIn("MODEL_default", self.html["modelFallback"])
        self.assertIn(self.T["help"]["configModelFallback"], self.html["modelFallback"])
        self.assertIn(self.T["help"]["configModelUnknown"], self.html["modelUnknownRole"])
        self.assertIn(self.T["help"]["configModelNoRoute"], self.html["modelUnknownRole"])

    def test_the_branches_read_as_the_definition_says(self):
        self.assertEqual(self.html["gatesFail"], self.T["help"]["configGoBack"].replace("{step}", "implement").replace("{n}", "2").replace("{to}", self.T["config"]["human"]))
        self.assertEqual(self.html["syncPass"], self.T["help"]["configGoStep"].replace("{step}", "pr"))
        self.assertEqual(self.html["researchFail"], self.T["help"]["configFailDefault"])
        self.assertEqual(self.html["automergePass"], self.T["help"]["configGoEnd"])
        self.assertEqual(self.html["automergeFail"], self.T["help"]["configGoHuman"])

    def test_a_definition_that_cannot_be_read_is_shown_not_hidden(self):
        self.assertIn("字下げが揃っていません", self.html["broken"])
        self.assertIn("$.steps[0].role", self.html["schemaErr"])
        self.assertIn(self.T["help"]["configSchemaErrors"], self.html["schemaErr"])
        self.assertEqual(self.html["clean"], "")

    def test_files_and_unknown_keys_are_shown_or_said_to_be_unset(self):
        self.assertIn("plan.md, research.md", self.html["files"])
        self.assertIn("report.md", self.html["files"])
        self.assertIn(self.T["config"]["none"], self.html["filesNone"])
        self.assertIn("whatever", self.html["unknown"])
        self.assertIn(self.T["help"]["configUnknownKeys"], self.html["unknown"])
        self.assertEqual(self.html["unknownNone"], "")


if __name__ == "__main__":
    unittest.main()
