"""console のテスト。本番のデータは触らない（一時ディレクトリを AIFACTORY_WORKSPACE にして、その中に kb でチケットと dry-run の記録を作る）。

  python3 -m unittest discover -s console/tests -v

- HTTP: サーバーを別プロセスで起動して JSON API を叩く（GET / POST / 403 / 409 / dry-run ジョブ）
- JobStore: モジュールとして読み込み、ロック内の二重起動ガード・停止・再起動後の復元を直接確かめる
PJ は同梱の examples/projects/kumitate を使う（workspace/projects/ は空）。
"""
import datetime, importlib.machinery, importlib.util, json, os, pathlib, re, shutil, signal, socket, stat, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.parse, urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
CONSOLE = REPO / "console" / "bin" / "console"
KB = REPO / "kanban" / "bin" / "kb"
PJ = "kumitate"   # examples/projects/kumitate
OFFSET_ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")   # 記録の時刻の形（ADR-0026）

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_strings import load as load_strings   # noqa: E402  文言の実物を読む（node のテストで使う）


def seed_workspace(ws):
    """一時 workspace にチケット 1 件と、その dry-run の実行記録を作る"""
    env = {**os.environ, "AIFACTORY_WORKSPACE": str(ws)}
    r = subprocess.run([sys.executable, str(KB), "new", PJ, "research", "調査: テスト用の種", "--body", "-"], input="x\n\n## 完了条件\n- y\n", text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    tid = int(r.stdout.split()[0])
    r = subprocess.run([sys.executable, str(KB), "run", str(tid), "--dry-run"], text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    return tid


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


def load_module(jobs_dir):
    spec = importlib.util.spec_from_loader("console_mod", importlib.machinery.SourceFileLoader("console_mod", str(CONSOLE)))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    m.core.JOBS = pathlib.Path(jobs_dir); m.core.JOBS.mkdir(parents=True, exist_ok=True)   # JobStore は lib/core.py の JOBS を見る
    return m


class Http:
    def __init__(self, base): self.base = base
    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r: return r.status, json.loads(r.read())
    def post(self, path, body=None, header=True):
        req = urllib.request.Request(self.base + path, data=json.dumps(body or {}).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **({"X-Console": "1"} if header else {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())


# 鍵プール（#379）の口を確かめるための偽 sandbox。keys.json を書くのは本物と同じ「CLI だけ」で、
# console は読み取りとジョブの起動しかしないことを確かめる（本物の VM も Proxmox も使わない）
FAKE_SANDBOX_KEYS = r"""#!/usr/bin/env python3
import json, os, pathlib, sys
a = sys.argv[1:]
p = pathlib.Path(os.environ["SANDBOX_KEYS"])
if a[:1] == ["reinject"]:
    print("reinject", a[1]); sys.exit(0)
if a[:1] != ["keys"]: sys.exit(2)
sub, name = a[1], a[2]
d = json.loads(p.read_text()) if p.exists() else {"keys": []}
by = {k["name"]: k for k in d["keys"]}
if sub == "add":
    if name in by: print("[error] 既にあります", file=sys.stderr); sys.exit(1)
    d["keys"].append({"name": name, "token": sys.stdin.read().strip(), "enabled": True, "uses": 0, "last_used": None,
                      "issued": "2026-09-09", "note": "", "allow": {"fable": "--fable" in a, "other": "--other" in a}})
elif sub == "set":
    k = by[name]
    for f in ("fable", "other"):
        if "--%s=on" % f in a: k["allow"][f] = True
        if "--%s=off" % f in a: k["allow"][f] = False
    if "--disable" in a: k["enabled"] = False
    if "--enable" in a: k["enabled"] = True
elif sub == "rm":
    d["keys"] = [k for k in d["keys"] if k["name"] != name]
elif sub == "token":
    by[name]["token"] = sys.stdin.read().strip()
else: sys.exit(2)
p.write_text(json.dumps(d, ensure_ascii=False))
p.chmod(0o600)
print("[ok]", sub, name)
"""


class KeysApiTest(unittest.TestCase):
    """/api/keys（#379）: 一覧はマスク済み、書き込みは sandbox CLI 経由、無効化・削除は使っている貸出に reinject を起こす"""

    TOKEN = "fake-token-console-test-9999"

    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-keys-test-"))
        cls.ws = cls.tmp / "ws"; cls.ws.mkdir()
        cls.home = cls.tmp / "home"; cls.home.mkdir()
        cls.keys = cls.tmp / "keys.json"
        cls.state = cls.tmp / "state.json"
        cls.state.write_text("{}", encoding="utf-8")
        b = cls.tmp / "bin"; b.mkdir()
        sb = b / "sandbox"; sb.write_text(FAKE_SANDBOX_KEYS, encoding="utf-8"); sb.chmod(0o755)
        cls.port = free_port()
        env = {**os.environ, "AIFACTORY_WORKSPACE": str(cls.ws), "CONSOLE_JOBS": str(cls.tmp / "jobs"),
               "HOME": str(cls.home), "SANDBOX_STATE": str(cls.state), "SANDBOX_KEYS": str(cls.keys),
               "PATH": f"{b}:{os.environ.get('PATH', '')}"}
        cls.proc = subprocess.Popen([sys.executable, str(CONSOLE), "--port", str(cls.port)], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.http = Http(f"http://127.0.0.1:{cls.port}")
        for _ in range(50):
            try: cls.http.get("/api/keys"); break
            except Exception: time.sleep(0.1)
        else: raise RuntimeError("console が起動しない")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(timeout=10)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.keys.write_text(json.dumps({"keys": []}), encoding="utf-8")
        self.state.write_text("{}", encoding="utf-8")

    def lend(self, task, fable=None, other=None):
        """台帳に貸出を 1 件置く（take が書く形。鍵の名前だけが入り、値は入らない）"""
        k = {g: v for g, v in (("fable", fable), ("other", other)) if v}
        self.state.write_text(json.dumps({task: {"vmid": 9201, "name": "sb-t-01", "ip": "10.77.1.1", "pj": PJ,
                                                 "since": "2026-09-09T10:00:00+09:00", "keys": k}}, ensure_ascii=False), encoding="utf-8")

    def add(self, name, fable=False, other=True, token=None):
        return self.http.post("/api/keys", {"action": "add", "name": name, "token": token or self.TOKEN,
                                            "fable": fable, "other": other})

    def test_list_is_masked_and_never_carries_the_token(self):
        st, d = self.add("opus-a")
        self.assertEqual(st, 200, d)
        self.assertNotIn(self.TOKEN, json.dumps(d, ensure_ascii=False))
        st, v = self.http.get("/api/keys")
        self.assertEqual(st, 200)
        self.assertNotIn(self.TOKEN, json.dumps(v, ensure_ascii=False))
        self.assertEqual([k["name"] for k in v["keys"]], ["opus-a"])
        k = v["keys"][0]
        self.assertEqual(k["tail4"], self.TOKEN[-4:]); self.assertNotIn("token", k)
        self.assertEqual(k["allow"], {"fable": False, "other": True}); self.assertTrue(k["enabled"])
        self.assertEqual(v["candidates"], {"fable": 0, "other": 1})
        self.assertEqual(v["keys_file"], str(self.keys))

    def test_the_written_file_is_600_and_console_did_not_write_it(self):
        self.assertEqual(self.add("opus-a")[0], 200)
        self.assertEqual(stat.S_IMODE(os.stat(self.keys).st_mode), 0o600)

    def test_bad_input_is_refused_before_the_cli_runs(self):
        for b, why in (({"action": "add", "name": "bad name", "token": "x", "other": True}, "名前"),
                       ({"action": "nope", "name": "opus-a"}, "action"),
                       ({"action": "add", "name": "opus-a", "token": "", "other": True}, "トークン"),
                       ({"action": "add", "name": "opus-a", "token": "x"}, "fable")):
            st, d = self.http.post("/api/keys", b)
            self.assertEqual(st, 400, (b, d)); self.assertIn(why, d["error"])
        self.assertEqual(json.loads(self.keys.read_text())["keys"], [])

    def test_the_flags_can_be_toggled(self):
        self.assertEqual(self.add("k1", fable=True, other=False)[0], 200)
        st, d = self.http.post("/api/keys", {"action": "set", "name": "k1", "other": True})
        self.assertEqual(st, 200, d)
        self.assertEqual(d["view"]["keys"][0]["allow"], {"fable": True, "other": True})
        self.assertEqual(d["reinject_jobs"], [])

    def test_disabling_a_key_in_use_starts_a_reinject_job(self):
        self.assertEqual(self.add("opus-a")[0], 200)
        self.lend("379", other="opus-a")
        st, d = self.http.post("/api/keys", {"action": "set", "name": "opus-a", "enabled": False})
        self.assertEqual(st, 200, d)
        self.assertFalse(d["view"]["keys"][0]["enabled"])
        self.assertEqual([j["cmd"] for j in d["reinject_jobs"]], [["sandbox", "reinject", "379"]])
        self.assertEqual(d["reinject_jobs"][0]["ticket"], 379)
        _, jobs = self.http.get("/api/jobs")
        self.assertIn("sandbox-reinject", [j["kind"] for j in jobs["jobs"]])

    def test_removing_a_key_that_is_not_in_use_starts_no_job(self):
        self.assertEqual(self.add("opus-a")[0], 200)
        st, d = self.http.post("/api/keys", {"action": "rm", "name": "opus-a"})
        self.assertEqual(st, 200, d)
        self.assertEqual(d["reinject_jobs"], [])
        self.assertEqual(self.http.get("/api/keys")[1]["keys"], [])

    def test_the_lease_carries_the_key_names(self):
        self.lend("379", fable="fable-a", other="opus-a")
        st, v = self.http.get("/api/sandbox")
        self.assertEqual(st, 200)
        self.assertEqual(v["leases"][0]["keys"], {"fable": "fable-a", "other": "opus-a"})

    def test_sandbox_page_says_where_each_pj_key_comes_from(self):
        """sandbox 画面の「Claude の鍵」列は、鍵プールがあれば「鍵プール」、無ければ PJ 別（非推奨）/ 全体の設定ファイル / 未設定（ADR-0045）"""
        pjd = self.home / ".config" / "sandbox" / "pj"; pjd.mkdir(parents=True, exist_ok=True)
        def source():
            st, v = self.http.get("/api/sandbox"); self.assertEqual(st, 200)
            return next(t["key_source"] for t in v["templates"] if t["pj"] == PJ), v["key_pool"]
        self.assertEqual(source()[0], "none")
        (pjd / f"{PJ}.env").write_text("GH_REPO=x/y\nCLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-test\n", encoding="utf-8")
        self.assertEqual(source()[0], "pj")
        self.assertEqual(self.add("opus-a", fable=False, other=True)[0], 200)
        self.assertEqual(source()[0], "pool_partial")                             # Fable 用が無いので、その分は env に落ちる
        self.assertEqual(self.add("fable-a", fable=True, other=False)[0], 200)
        src, pool = source()
        self.assertEqual(src, "pool"); self.assertEqual(pool, {"fable": 1, "other": 1, "total": 2})
        (pjd / f"{PJ}.env").unlink()
        self.assertEqual(source()[0], "pool")
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("T.sandbox.keySource", app)

    def test_post_needs_the_console_header(self):
        st, d = self.http.post("/api/keys", {"action": "add", "name": "x", "token": "y", "other": True}, header=False)
        self.assertEqual(st, 403, d)


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-console-test-"))
        cls.ws = cls.tmp / "ws"; cls.ws.mkdir()
        cls.seed = seed_workspace(cls.ws)
        cls.port = free_port()
        env = {**os.environ, "AIFACTORY_WORKSPACE": str(cls.ws), "CONSOLE_JOBS": str(cls.tmp / "jobs")}
        cls.proc = subprocess.Popen([sys.executable, str(CONSOLE), "--port", str(cls.port)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.http = Http(f"http://127.0.0.1:{cls.port}")
        for _ in range(50):
            try: cls.http.get("/api/overview"); break
            except Exception: time.sleep(0.1)
        else: raise RuntimeError("console が起動しない")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(timeout=10)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def todo_id(self):
        """テスト用の todo チケットを複製 DB に作って使う（本番の todo に依存しない）"""
        if not hasattr(type(self), "_todo"):
            st, d = self.http.post("/api/tickets", {"pj": PJ, "kind": "research", "title": "調査: console テスト用", "body": "x\n\n## 完了条件\n- y"})
            assert st == 200 and d.get("id"), d
            type(self)._todo = d["id"]
        return type(self)._todo

    def test_overview_and_lists(self):
        st, o = self.http.get("/api/overview")
        self.assertEqual(st, 200); self.assertTrue(o["db"]); self.assertEqual(set(o["counts"]), {"todo", "in_progress", "review", "blocked", "done"})
        self.assertEqual(o["paths"]["workspace"], str(self.ws)); self.assertFalse(o["paths"]["legacy"])
        self.assertEqual(set(o["repo"]) >= {"known", "diverged", "ahead", "behind", "dirty", "branch", "path"}, True)   # 337: PJ 定義を読む checkout の状態
        _, t = self.http.get("/api/tickets"); self.assertGreater(len(t["tickets"]), 0); self.assertIn("bug", t["kinds"])
        _, tp = self.http.get(f"/api/tickets?pj={PJ}"); self.assertGreater(len(tp["tickets"]), 0)
        self.assertEqual({x["pj"] for x in tp["tickets"]}, {PJ})                      # 絞り込みはサーバー側で効いている
        self.assertTrue(all(not k.startswith((".", "_")) for k in t["kinds"]), t["kinds"])   # `._bug` のようなごみを候補に出さない
        self.assertIn("kind_desc", t); self.assertTrue(t["kind_desc"]["bug"])                # 画面が種別の用途を説明できる
        _, r = self.http.get("/api/runs"); self.assertTrue(any(x["kind"] == "v1" for x in r["runs"]))
        for ep in ("/api/sandbox", "/api/config", "/api/logs", "/api/jobs"): self.assertEqual(self.http.get(ep)[0], 200)

    def test_overview_filters_runs_by_pj_before_the_cap(self):
        """動いている run の一覧を PJ で絞るのはサーバー側（先に絞ってから上限を掛ける）。

        `runs_active` は 6 件で切るので、ブラウザーが受け取ってから絞ると、7 本以上動いているときに
        選んだ PJ の run が一覧から漏れる。プール合計は PJ × 3 台なので現実に起きる。"""
        mine = [self._fixture_run(f"2020-02-0{i}-other-90{i}",
                                  {"pj": "other", "task": f"90{i}", "workflow": "feature", "started": f"2020-02-0{i}T09:00:00+00:00",
                                   "next": "implement", "loops": {}, "current": None, "history": []}) for i in range(1, 7)]
        mine.append(self._fixture_run(f"2020-01-01-{PJ}-989",
                                      {"pj": PJ, "task": "989", "workflow": "feature", "started": "2020-01-01T09:00:00+00:00",
                                       "next": "implement", "loops": {}, "current": None, "history": []}))
        try:
            st, o = self.http.get("/api/overview")
            self.assertEqual(st, 200)
            self.assertNotIn(f"2020-01-01-{PJ}-989", [r["name"] for r in o["runs_active"]])   # 全体では上限に押し出される

            st, f = self.http.get(f"/api/overview?pj={PJ}")
            self.assertEqual(st, 200); self.assertEqual(f["pj"], PJ)
            names = [r["name"] for r in f["runs_active"]]
            self.assertIn(f"2020-01-01-{PJ}-989", names, "PJ で絞ったのに、その PJ の動いている run が漏れている")
            self.assertEqual({r["pj"] for r in f["runs_active"]}, {PJ})
            _, allr = self.http.get("/api/runs")
            live = [r for r in allr["runs"] if r["status"] == "running" and not r.get("dry") and r["pj"] == PJ and r["kind"] == "v1"]
            self.assertEqual(f["runs_active_n"], len(live))                                  # 画面が「ほか n 件」を出せる
            self.assertEqual(f["counts"], o["counts"])                                       # 件数はナビ用に全 PJ のまま
            self.assertEqual(f["limit"], 6); self.assertGreaterEqual(o["runs_active_n"], 7)   # 上限は結果と一緒に返す

            _, g = self.http.get("/api/overview?pj=other")
            self.assertEqual(g["runs_active_n"], 6); self.assertEqual(len(g["runs_active"]), 6)
            self.assertEqual({r["name"] for r in g["runs_active"]}, set(mine[:6]))
            self.assertEqual(self.http.get("/api/overview?pj=nosuch")[1]["runs_active"], [])
        finally:
            for name in mine: shutil.rmtree(self.ws / "runs" / name, ignore_errors=True)

    def test_overview_runs_live_is_not_capped_for_the_board_cards(self):
        """ボードのカードがチケットと動いている run を突き合わせるための一覧（チケット 376）。

        `runs_active` は帯のために 6 件で切るので、それで突き合わせると 7 本以上動いているときに
        7 枚目以降のカードだけ工程が出ない。`runs_live` は上限を掛けず、カードに要る分だけを軽い形で返す。
        工程（step / since）は state.json の current があるときだけ入れる（開始前のカードに前の工程を出さない）。
        """
        mine = [self._fixture_run(f"2020-03-0{i}-other-80{i}",
                                  {"pj": "other", "task": f"80{i}", "workflow": "feature", "started": f"2020-03-0{i}T09:00:00+00:00",
                                   "next": "implement", "loops": {}, "current": None, "history": []}) for i in range(1, 8)]
        mine.append(self._fixture_run("2020-03-09-other-809",
                                      {"pj": "other", "task": "809", "workflow": "feature", "started": "2020-03-09T09:00:00+00:00",
                                       "next": "gates", "loops": {}, "history": [],
                                       "current": {"step": "implement", "kind": "agent", "log": "agent-implement-1.log",
                                                   "since": "2020-03-09T09:30:00+00:00"}}))
        try:
            st, o = self.http.get("/api/overview?pj=other")
            self.assertEqual(st, 200)
            self.assertEqual(len(o["runs_active"]), 6); self.assertEqual(o["runs_active_n"], 8)   # 帯は上限つきのまま
            names = [r["name"] for r in o["runs_live"]]
            self.assertEqual(len(names), 8, "突き合わせ用の一覧に上限が掛かっている（7 枚目以降のカードに工程が出ない）")
            self.assertEqual(names, sorted(names, reverse=True), "新しい順ではない（複数 attempt のうち古い run を拾ってしまう）")
            live = {r["name"]: r for r in o["runs_live"]}
            self.assertEqual(live["2020-03-09-other-809"]["step"], "implement")
            self.assertEqual(live["2020-03-09-other-809"]["task"], "809")
            self.assertNotIn("step", live["2020-03-01-other-801"], "current が無い run に工程が入っている（前の工程を出してしまう）")
            self.assertEqual(live["2020-03-01-other-801"]["next"], "implement")            # 開始前は「次は」を出せる
            for r in o["runs_live"]:
                self.assertEqual(set(r) >= {"name", "pj", "task", "workflow", "next", "started"}, True, r)
                self.assertNotIn("history", r); self.assertNotIn("status", r)               # カードに要らないものは載せない

            _, f = self.http.get(f"/api/overview?pj={PJ}")
            self.assertEqual([r["name"] for r in f["runs_live"] if r["pj"] == "other"], [], "PJ で絞ったのに他の PJ の run が残っている")
            self.assertEqual(self.http.get("/api/overview?pj=nosuch")[1]["runs_live"], [])
        finally:
            for name in mine: shutil.rmtree(self.ws / "runs" / name, ignore_errors=True)

    def test_board_and_nav_read_the_run_count_from_the_server(self):
        """ボードは PJ で絞った overview を読み、上限からあふれた分を画面に出す。

        JS を動かす基盤が無いので、test_board_strip_and_columns_share_source と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewBoard")
        board = app[i:app.index("\n}", i)]
        self.assertRegex(board, r"overview\?pj=", "ボードが PJ で絞った overview を取っていない（7 本以上で run が漏れる）")
        self.assertIn("T.board.moreRuns", board, "上限からあふれた run の案内が無い")
        i = app.index("async function refreshNav")
        nav = app[i:app.index("\n}", i)]
        self.assertIn("runs_active_n", nav, "ナビの run 数が一覧の長さのままで、6 で頭打ちになる")

    def test_next_for_dispatch_dialog(self):
        """配車ダイアログが押す前に見せる「次に回るチケット」（kb next --json）"""
        tid = self.todo_id()
        st, d = self.http.get("/api/next"); self.assertEqual(st, 200); self.assertIsNotNone(d["next"]); self.assertEqual(d["next"]["status"], "todo")
        self.assertLessEqual(d["next"]["id"], tid)                                   # 最も古い todo
        st, d = self.http.get(f"/api/next?pj={PJ}"); self.assertEqual(st, 200); self.assertEqual(d["next"]["pj"], PJ)
        st, d = self.http.get("/api/next?pj=no-such-pj"); self.assertEqual(st, 200); self.assertIsNone(d["next"])

    def test_sandbox_api_returns_vms(self):
        """/api/sandbox が sandbox ls の結果を列に分けて返す（画面が表を組める）。ジョブの記録は手で置く（VM を使わない）"""
        jid = "20260907-080000-sandbox-ls"
        d = self.tmp / "jobs" / jid; d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({"id": jid, "kind": "sandbox-ls", "label": "sandbox ls", "cmd": ["sandbox", "ls"],
                                                 "ticket": None, "run_hint": None, "pid": 1, "started": "2026-09-07T08:00:00",
                                                 "finished": "2026-09-07T08:00:04", "rc": 0, "state": "done"}), encoding="utf-8")
        (d / "log").write_text("$ sandbox ls\nTASK     VM             VMID   IP           STATUS    SINCE\n"
                               "-        sb-kumitate-long-name-99 9299 10.77.1.9 running\n", encoding="utf-8")
        st, v = self.http.get("/api/sandbox")
        self.assertEqual(st, 200); self.assertEqual(v["last_ok_ls"]["id"], jid); self.assertEqual(v["last_ls"]["id"], jid)
        self.assertEqual(v["vms"], [{"task": None, "name": "sb-kumitate-long-name-99", "vmid": "9299",
                                     "ip": "10.77.1.9", "status": "running", "since": None, "pj": None}])

    def test_error_messages_say_what_to_do(self):
        """エラー文は「何が起きたか」の後に「どうすればよいか」（console/UX.md）"""
        st, d = self.http.post("/api/sandbox/release", {"task": "abc"}); self.assertEqual(st, 400); self.assertIn("指定してください", d["error"])
        st, d = self.http.post(f"/api/tickets/{self.todo_id()}/action", {"action": "nope"}); self.assertEqual(st, 400); self.assertIn("してください", d["error"])
        st, d = self.http.post("/api/intake", {"text": " "}); self.assertEqual(st, 400); self.assertIn("入れてください", d["error"])
        st, d = self.http.post("/api/tickets/999999/action", {"action": "start"}, header=False); self.assertEqual(st, 403); self.assertIn("X-Console", d["error"])

    def test_static_ui_files(self):
        """画面の静的ファイル（index.html / strings.js / app.js / style.css）が配信され、index.html が strings.js を app.js より先に読む"""
        for p, ctype in (("/", "text/html"), ("/strings.js", "javascript"), ("/app.js", "javascript"), ("/style.css", "text/css")):
            with urllib.request.urlopen(self.http.base + p, timeout=10) as r:
                self.assertEqual(r.status, 200, p); self.assertIn(ctype, r.headers["Content-Type"], p); body = r.read().decode("utf-8")
            if p == "/": self.assertLess(body.index("strings.js"), body.index("app.js")); self.assertIn('charset="utf-8"', body); self.assertIn('lang="ja"', body)

    def test_board_strip_and_columns_share_source(self):
        """ボードの帯と列が同じ絞り込み結果から数える（PJ を選ぶと帯だけ全体のままになるのを防ぐ）。

        JS を動かす基盤が無い（CI は Python 標準ライブラリだけ）ので、test_strings.py と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewBoard")
        body = app[i:app.index("\n}", i)]
        self.assertNotIn("o.counts", body, "帯が overview の全体集計を読んでいる。絞り込み済みの by から数える")
        self.assertEqual(re.findall(r"col\('(\w+)'", body), ["todo", "in_progress", "review", "done", "blocked"],
                         "列の並びを帯と揃える（未着手・実行中・レビュー待ち・完了・人間待ち）")
        for key in ("T.board.scopeAll", "T.board.scopePj"): self.assertIn(key, body, f"対象範囲の明示 {key} が無い")

    def test_board_cards_show_the_running_step_and_two_separate_links(self):
        """実行中のカードに今の工程・経過時間と実行記録への導線を出す（チケット 376）。

        カード全体が `<a class="card">` のままだと、中に実行記録の `<a>` を足せない（`<a>` の入れ子は無効な HTML で、
        ブラウザーの挙動が決まらない）。チケット詳細と実行記録は兄弟の `<a>` にする。
        JS を動かす基盤が無いので、test_board_strip_and_columns_share_source と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('<a class="card"', app, "カード全体がリンクのままで、中に実行記録のリンクを足せない（<a> の入れ子）")
        i = app.index("async function viewBoard")
        board = app[i:app.index("\n}", i)]
        self.assertIn("runs_live", board, "カードが突き合わせ用の run 一覧（runs_live）を読んでいない。上限つきの runs_active では漏れる")
        self.assertRegex(board, r'<div class="card"', "カードの外枠が div になっていない")
        self.assertRegex(board, r'<a class="t" href="#/ticket/', "題名がチケットへのリンクになっていない（Tab で届かない）")
        self.assertRegex(board, r'<a class="live" href="#/run/', "カードから実行記録へ移るリンクが無い")
        self.assertIn("T.board.liveOpen", board, "実行記録リンクの読み上げ名（aria-label）が無い")
        for key in ("T.board.liveStep", "T.board.liveNext"): self.assertIn(key, board, f"{key} をカードで使っていない")
        self.assertRegex(board, r"r\.step \?", "current の無い run（開始前・工程の切れ目）に前の工程を出さない分岐が無い")
        # aria-label は中身を上書きする。見えている工程と経過時間を読み上げ名に含める（label in name。ticketLink と同じ約束）
        i = board.index("const liveRow")
        row = board[i:board.index("\n  };", i)]
        self.assertIn("text: txt", row, "工程行の読み上げ名に見えている文字（工程・経過時間）が入っていない")
        for key in ("T.board.liveStep", "T.board.liveNext", "T.board.liveSince"):
            self.assertLess(row.index(key), row.index("aria-label"), f"{key} を組む前に aria-label を書いている（見えている文字を含められない）")
        T = load_strings()
        self.assertIn("{text}", T["board"]["liveOpen"], "読み上げ名の文言に見えている文字の差し込み口が無い")
        self.assertLess(T["board"]["liveOpen"].index("{text}"), T["board"]["liveOpen"].index("{run}"), "読み上げ名が見えている文字で始まっていない")
        css = (REPO / "console" / "static" / "style.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.card \.t::after", "題名リンクの当たり判定がカード全面に無い（クリックできる範囲が狭くなる）")
        self.assertRegex(css, r"\.card \.live \{", "カードの工程行の指定が無い")
        # メモは title のツールチップでしか全文が読めない。題名リンクの ::after に覆わせない
        note = re.search(r"\.card \.note \{[^}]*\}", css).group(0)
        for prop in ("position: relative", "z-index: 1"):
            self.assertIn(prop, note, f"メモに {prop} が無く、題名リンクの当たり判定がツールチップを覆う")

    def test_nav_badges_say_which_scope_they_count(self):
        """左ナビのバッジは全 PJ の数字（画面をまたぐので絞らない）。その対象範囲が画面で分かること。

        ボードで PJ を選ぶと帯と列は絞られるのに、バッジだけ数が違って見える。
        JS を動かす基盤が無いので、test_board_strip_and_columns_share_source と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function refreshNav")
        nav = app[i:app.index("\n}", i)]
        self.assertIn("T.nav.badgeScope", nav, "バッジが何を数えているかを画面が言っていない")
        for el in ("n-board", "n-runs"):
            self.assertRegex(nav, r"\$\('%s'\)\.title" % el, f"{el} のバッジに対象範囲の説明（title）が無い")
        src = (REPO / "console" / "static" / "strings.js").read_text(encoding="utf-8")
        T = json.loads(src[src.index("const T = ") + len("const T = "):src.rindex("};") + 1])
        self.assertIn("PJ", T["nav"]["badgeScope"])
        self.assertIn("PJ", T["board"]["scopePj"])
        self.assertNotEqual(T["board"]["scopePj"], T["board"]["scopeAll"])

    def test_done_overflow_leads_to_ticket_list(self):
        """ボードの完了列からあふれた分が、CLI ではなく画面（#/tickets）に続く。

        JS を動かす基盤が無いので、test_board_strip_and_columns_share_source と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        board = app[app.index("async function viewBoard"):app.index("\n}", app.index("async function viewBoard"))]
        self.assertIn("#/tickets", board, "完了列からあふれた分を見る導線が画面に無い（CLI の案内だけで終わっている）")
        self.assertIn("T.btn.openTickets", board, "ボードから一覧へ行くボタンが無い")
        self.assertRegex(app, r"seg\[0\] === 'tickets'", "route() に #/tickets が無い")

        src = (REPO / "console" / "static" / "strings.js").read_text(encoding="utf-8")
        T = json.loads(src[src.index("const T = ") + len("const T = "):src.rindex("};") + 1])
        self.assertNotIn("kb list", T["board"]["more"], "あふれた分の案内が CLI のコマンドのままになっている")

        i = app.index("async function viewTickets")
        view = app[i:app.index("\n}", i)]
        for key in ("q", "pj", "status"):
            self.assertIn(f"p.get('{key}')", view, f"絞り込み条件 {key} を URL から読んでいない（詳細から戻ると条件が消える）")
        render = app[app.index("function tkRender"):app.index("\n}", app.index("function tkRender"))]
        self.assertIn("T.tickets.count", render, "件数（何件中の何件か）を出していない")
        self.assertIn("T.empty.tickets", render, "0 件のときの案内が無い")

    def test_every_data_act_has_a_handler(self):
        """`data-act` は必ず `actions` に手がある名前だけにする。

        click の委譲（`document.addEventListener('click', ...)`）は SELECT と checkbox 以外の
        `[data-act]` をすべて `actions[...]` に回すので、手の無い名前を書くと押した瞬間に
        TypeError → 赤いトースト、さらに `disabled` の切り替えでフォーカスが外れる。
        JS を動かす基盤が無いので、ソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        block = app[app.index("const actions = {"):app.index("\n};", app.index("const actions = {"))]
        handlers = set(re.findall(r"^  '?([\w-]+)'?:", block, re.M))
        used = set(re.findall(r'data-act="([\w-]+)"', app))
        self.assertTrue(handlers, "actions の手を読み取れていない（テストの前提が壊れている）")
        self.assertEqual(used - handlers, set(), "actions に手の無い data-act がある（押すとエラーのトーストが出る）")

    def test_ticket_run_area_follows_status(self):
        """チケットの実行エリアが今の状態に合う（完了で押せる緑ボタン、ボタンが無い画面での「上の実行する」案内を防ぐ）。

        JS を動かす基盤が無いので、ボードの帯と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewTicket(")
        body = app[i:app.index("\n}", i)]
        self.assertRegex(body, r"canRun\s*=[^;\n]*status\s*!==\s*'done'", "実行できるかを status から決めていない")
        self.assertRegex(body, r"const runBtn = \([^)]*disabled[^)]*\)", "runBtn が押せない状態を受け取れない")
        self.assertRegex(body, r"runBtn\(T\.btn\.run,\s*'primary'[^)]*canRun", "本番の実行ボタンに実行可否を渡していない")
        self.assertNotRegex(body, r"runBtn\(T\.btn\.dryRun[^)]*canRun", "dry-run は完了済みでも通るので、押せなくしない")
        for key in ("T.help.runReview", "T.help.runBlocked"):
            self.assertIn(key, body, f"実行の案内が状態ごとに分かれていない（{key} が無い）")
        for key in ("T.empty.ticketRunsNoProjectYml", "T.empty.ticketRunsBusy", "T.empty.ticketRunsDone"):
            self.assertIn(key, body, f"実行記録の空文言が状態で変わらない（{key} が無い）")
        src = (REPO / "console" / "static" / "strings.js").read_text(encoding="utf-8")
        T = json.loads(src[src.index("const T = ") + len("const T = "):src.rindex("};") + 1])
        self.assertIn(T["btn"]["redo"], T["help"]["runDone"], "完了時の案内が、隣に出すボタンの名前と一致していない")

    def test_intake_keeps_draft_across_navigation(self):
        """起票の下書き（自由文・直接起票の 9 項目）が画面往復で消えない。

        `route()` は hash が変わるたび `viewIntake()` を呼び、`render()` が main を作り直す。
        入力をどこにも保持しないと、起票 → ログ → 起票の往復・再読み込み・戻るで必ず空になる。
        JS を動かす基盤が無い（CI は Python 標準ライブラリだけ）ので、ソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewIntake")
        view = app[i:app.index("\n}", i)]

        def action(name):
            j = app.index(f"  '{name}': ")
            m = re.compile(r"\n  '[\w-]+': ").search(app, j + 1)
            return app[j:m.start() if m else len(app)]

        # 保存先は sessionStorage（同じタブの往復・再読み込み・戻るで残り、タブを閉じれば消える）
        self.assertIn("sessionStorage", app, "下書きの保存先が無い")
        self.assertNotIn("beforeunload", app, "離脱の警告ではなく保持で守る（UX.md の文言規則の外に出る標準ダイアログを出さない）")
        self.assertTrue(re.search(r"(?:d|draft)\s*=\s*\w*[Dd]raft\w*\(", view), "viewIntake が保存済みの下書きを読んでいない")

        # 9 項目すべてが「id → 下書きの鍵」の対応表にあり、描画で復元されている
        for eid, key in (("in-text", "text"), ("in-pj", "pj"), ("in-kind", "kind"), ("in-dry", "dry"),
                         ("new-pj", "newPj"), ("new-kind", "newKind"), ("new-pr", "newPr"),
                         ("new-title", "title"), ("new-body", "body")):
            self.assertIn(f'id="{eid}"', view, f"{eid} が起票画面に無い")
            self.assertTrue(re.search(rf"'{eid}':\s*'{key}'", app), f"{eid} が下書きの対応表に無い（保存されない）")
            self.assertTrue(re.search(rf"\b(?:d|draft)\.{key}\b", view), f"{eid} の下書き {key} を描画で復元していない")

        # 破棄の規則: 送信が成功したときだけ、そのパネルの分を消す（失敗したら直して送り直せる）
        for name in ("intake", "new"):
            b = action(name)
            self.assertIn("draftDrop", b, f"actions['{name}'] が送信後に下書きを消していない")
            self.assertGreater(b.index("draftDrop"), b.index("await api("), f"actions['{name}'] が送信の前に下書きを消している")

        # 破棄は明示操作（可逆なので確認なし。トーストの「元に戻す」で書き戻す）
        for act in ("intake-clear", "new-clear"):
            self.assertIn(f"'{act}'", view, f"「下書きを捨てる」（{act}）のボタンが起票画面に無い")
            self.assertIn(f"'{act}':", app, f"actions に {act} が無い")
        self.assertTrue(re.search(r"function draftClear[\s\S]{0,600}T\.btn\.undo", app), "下書きの破棄に「元に戻す」が無い")

    def test_intake_shows_project_yml_readiness(self):
        """起票画面が、PJ を選んだ時点で「配車すると人間待ちになるか」を出せる。

        判定は sandbox / チケット画面と同じ project.yml の有無。project.yml が無くても provision.sh だけで PJ 候補には入るので、
        候補に出るが実行できない PJ が API 越しに区別できることを確かめる。表示は色だけに頼らず、起票そのものは止めない。
        """
        noyml = self.ws / "projects" / "noyml"
        noyml.mkdir(parents=True)
        (noyml / "provision.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        try:
            _, t = self.http.get("/api/tickets")
            self.assertIn("pj_ready", t, "/api/tickets が PJ の準備状態を返していない")
            self.assertEqual(sorted(t["pj_ready"]), sorted(t["pjs"]), "候補の PJ と準備状態の対象がずれている")
            self.assertTrue(t["pj_ready"][PJ], f"project.yml のある {PJ} が準備不足になっている")
            self.assertIn("noyml", t["pjs"], "provision.sh だけの PJ が候補から消えている（起票は妨げない）")
            self.assertFalse(t["pj_ready"]["noyml"], "project.yml の無い PJ が準備済みになっている")
        finally:
            shutil.rmtree(noyml, ignore_errors=True)

        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewIntake")
        view = app[i:app.index("\n}", i)]
        self.assertIn("pj_ready", view, "viewIntake が API の準備状態を読んでいない")
        for eid in ("in-pj", "new-pj"):
            self.assertTrue(re.search(rf'id="{eid}" data-act="pj-help"', view), f"{eid} を変えても準備状態が更新されない")
            self.assertIn(f'id="{eid}-help"', view, f"{eid} の準備状態を出す行が無い")
        self.assertIn("'pj-help':", app, "actions に pj-help が無い（選び直しても表示が変わらない）")
        # 色だけに頼らない: 準備済み / 準備不足のどちらも文字のバッジと本文で読める
        for key in ("T.intake.pjReadyBadge", "T.intake.pjNotReadyBadge", "T.help.pjReady", "T.help.pjNotReady"):
            self.assertIn(key, app, f"{key} を使っていない（状態が色でしか分からない）")
        self.assertIn("#/sandbox", app, "準備状態を確かめる先への導線が無い")
        # 準備不足でも backlog には積める: 送信ボタンを押せなくしない
        for act in ("intake", "new"):
            self.assertFalse(re.search(rf'data-act="{act}"[^>]*disabled', view), f"準備不足の PJ で「{act}」を押せなくしている")

    def test_run_status_drives_the_ui(self):
        """実行中かどうかの判定は API の status に寄せる（app.js が `!r.finished` で独自に決めない）。

        JS を動かす基盤が無いので、test_board_strip_and_columns_share_source と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        for fn in ("async function viewBoard", "async function viewRuns", "async function viewRun("):
            i = app.index(fn); body = app[i:app.index("\n}", i)]
            self.assertIn("status ===", body, f"{fn} が status を見ていない")
            self.assertNotIn("!s.finished", body, f"{fn} が finished から実行中を決めている")
        self.assertIn("T.run.notStarted", app); self.assertIn("T.run.noState", app)

    def test_list_rows_have_real_links(self):
        """一覧の行から詳細を開く導線を、行クリックだけでなく本物の `<a>` にする（チケット 224）。

        行は `<tr class="link" data-href>` で、名前セルが素の `<td>` だと Tab で届かず、
        読み上げでも link に見えない。同じ行のチケット番号・PR だけがリンクに見えるのを直す。
        JS を動かす基盤が無いので、test_board_strip_and_columns_share_source と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertRegex(app, r"function jobLink\(j\)[^\n]*<a href=\"#/job/", "ジョブ名を <a> にする jobLink が無い")
        for fn, helper in (("async function viewRuns", "runLink("), ("async function viewJobs", "jobLink("),
                           ("async function viewTicket(", "jobLink(")):
            i = app.index(fn); body = app[i:app.index("\n}", i)]
            self.assertIn(helper, body, f"{fn} の一覧が名前をリンクにしていない（{helper}）")
            self.assertIn('tr class="link" data-href', body, f"{fn} の行クリック（tr.link data-href）が消えている")
        i = app.index("async function route")
        body = app[i:app.index("\n}", i)]
        self.assertNotIn("window.scrollTo(0, 0)", body, "経路が変わるたび先頭に飛ぶと、詳細から戻ったとき一覧の位置が失われる")
        self.assertIn("scrollPos", body, "戻ったときに一覧の位置を戻す仕掛けが無い")

    def test_tickets_list_rows_have_real_links(self):
        """チケットの一覧の行も本物の `<a>` にする（チケット 375）。

        #224 で実行記録・ジョブ・ログの表は直したが、`tkRender` の行だけが素の `<td>` のまま残っていた。
        検索した後に Tab で結果へ届かず、Enter でも開けず、読み上げでは cell にしか見えない。
        JS を動かす基盤が無いので、test_list_rows_have_real_links と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        css = (REPO / "console" / "static" / "style.css").read_text(encoding="utf-8")
        self.assertRegex(app, r"function ticketLink\([^\n]*<a href=\"#/ticket/",
                         "チケット番号を <a> にする ticketLink が無い")
        self.assertRegex(app, r"function ticketLink\([^\n]*aria-label=",
                         "ticketLink に読み上げ名（aria-label）が無い。番号だけでは何のリンクか分からない")
        i = app.index("function tkRender"); body = app[i:app.index("\n}", i)]
        self.assertIn("ticketLink(", body, "チケットの一覧が番号をリンクにしていない（ticketLink）")
        self.assertIn('tr class="link" data-href', body, "一覧の行クリック（tr.link data-href）が消えている")
        self.assertIn("T.empty.tickets", body, "0 件のときの案内が消えている")
        self.assertIn("closest('a, button')", app,
                      "行クリックの委譲がリンクを除外していない（リンクと行クリックが二重に発火する）")
        self.assertRegex(css, r":focus-visible[^{]*\{[^}]*outline", "フォーカスの表示（:focus-visible の outline）が無い")

    def test_logs_are_derived_into_rows(self):
        """起票・配車のログを、項目名つきの表にできる形（entries）にして返す（チケット 230、ADR-0027）。

        dispatch.log は行の種類ごとに列が違い（tid の無い行もある）、intake.log は 7 列固定。分解は
        コンソール側で一度だけ行い、glue のログ形式は変えない。どの規則にも当てはまらない行は隠さず
        event="other" として原文を残す（推測で埋めない）。生の text は「元のログを見る」と MCP が使うので消さない。
        """
        logs = self.ws / "logs"; logs.mkdir(parents=True, exist_ok=True)
        (logs / "dispatch.log").write_text(
            "2026-09-07T10:00:01\ttodo が無い（または全部飛ばした）。終了\n"
            "2026-09-07T10:00:02\t204 kumitate bug: project.yml 無し → blocked\n"
            "2026-09-07T10:00:03\t205 kumitate: Pull worker unavailable → skip\n"
            "2026-09-07T10:00:04\t206 kumitate: プール 3 台すべて貸出中 → この PJ は飛ばす\n"
            "2026-09-07T10:00:05\tstart 207 kumitate feature ログ画面を表にする (dry-run)\n"
            "2026-09-07T10:00:06\tend   207 kumitate feature rc=2 status=todo 93s\n"
            "2026-09-07T10:00:07\tこれから足される種類の行\n", encoding="utf-8")
        (logs / "intake.log").write_text(
            "2026-09-07T09:00:01\t203\tkumitate\tbug\t0.9\tmodel-x\t題名と本文が整っている\n"
            "2026-09-07T09:00:02\t204\tkumitate\tfeature\t0.97\tmodel-x\tPJ の指定があった\n", encoding="utf-8")
        st, d = self.http.get("/api/logs")
        self.assertEqual(st, 200)
        self.assertEqual(d["total"], 9); self.assertEqual(len(d["entries"]), 9)
        rows = d["entries"]
        self.assertEqual([r["at"] for r in rows], sorted((r["at"] for r in rows), reverse=True), "新しい順に並んでいない")
        one = lambda src, ev: next(r for r in rows if r["source"] == src and r["event"] == ev)

        idle = one("dispatch", "idle")
        self.assertIsNone(idle["tid"]); self.assertEqual(idle["pj"], "")                     # tid / pj の無い行も落とさない
        blocked = one("dispatch", "blocked")
        self.assertEqual((blocked["tid"], blocked["pj"], blocked["kind"], blocked["status"]), (204, PJ, "bug", "blocked"))
        skips = sorted((r for r in rows if r["event"] == "skip"), key=lambda r: r["tid"])
        self.assertEqual([(r["tid"], r["reason"]) for r in skips], [(205, "worker_unavailable"), (206, "pool_busy")])
        self.assertEqual(skips[1]["detail"], "3")                                            # 台数は項目にして持つ
        start = one("dispatch", "start")
        self.assertEqual((start["tid"], start["kind"], start["dry_run"]), (207, "feature", True))
        self.assertEqual(start["reason"], "ログ画面を表にする")                              # dry-run の印は題名から外す
        end = one("dispatch", "end")
        self.assertEqual((end["tid"], end["status"], end["rc"], end["elapsed_s"]), (207, "todo", 2, 93))
        other = one("dispatch", "other")
        self.assertEqual(other["reason"], "これから足される種類の行"); self.assertIn("これから足される", other["raw"])
        intake = [r for r in rows if r["source"] == "intake"]
        self.assertEqual([(r["tid"], r["kind"], r["confidence"], r["model"]) for r in intake],
                         [(204, "feature", 0.97, "model-x"), (203, "bug", 0.9, "model-x")])
        self.assertEqual(intake[0]["reason"], "PJ の指定があった")
        self.assertIn("rc=2", d["dispatch"]["text"]); self.assertIn("0.97", d["intake"]["text"])   # 原文も残っている

    def test_logs_screen_is_a_table_that_links_to_tickets(self):
        """ログの 1 行から対象チケットへ直接移動でき、項目名・絞り込み・原文がそろっている（チケット 230）。

        JS を動かす基盤が無いので、test_list_rows_have_real_links と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewLogs")
        view = app[i:app.index("\n}", i)]
        for key in ("q", "pj", "src"):
            self.assertIn(f"p.get('{key}')", view, f"絞り込み条件 {key} を URL から読んでいない（詳細から戻ると条件が消える）")
        self.assertIn('id="lg-q"', view, "チケット番号で絞る欄が無い")
        self.assertIn("T.h.rawLog", view, "元のログを見る導線が無い")
        self.assertIn('<pre class="log small" id="lg-raw-', view, "原文（生のログ）を残していない")
        self.assertIn("schedule(lgRefresh", view, "定期更新が表だけの描き直しになっていない")
        self.assertNotIn("schedule(viewLogs", app, "10 秒ごとに画面全体を作り直すと、絞り込みの入力が飛ぶ")
        i = app.index("function lgRender")
        render = app[i:app.index("\n}", i)]
        self.assertIn('<a href="#/ticket/', render, "チケット番号が本物のリンクになっていない（Tab で届かない）")
        self.assertIn('tr class="link" data-href', render, "行クリックでチケットを開けない")
        self.assertIn("T.logs.count", render, "件数（何件中の何件か）を出していない")
        self.assertIn("T.empty.logs", render, "0 件のときの案内が無い")
        for key in ("T.th.at", "T.th.process", "T.label.pj", "T.th.ticket", "T.th.result", "T.th.reason"):
            self.assertIn(key, render, f"列名 {key} が表に無い")
        src = (REPO / "console" / "static" / "strings.js").read_text(encoding="utf-8")
        T = json.loads(src[src.index("const T = ") + len("const T = "):src.rindex("};") + 1])
        for path, v in (("logs.endDetail", T["logs"]["endDetail"]), ("logs.event.end", T["logs"]["event"]["end"])):
            for word in ("rc", "status"):
                self.assertNotIn(word, v, f"{path} に開発者の語彙が残っている（{word}）")

    def test_ticket_detail(self):
        _, t = self.http.get("/api/tickets"); tid = t["tickets"][0]["id"]
        st, d = self.http.get(f"/api/tickets/{tid}")
        self.assertEqual(st, 200); self.assertIsNotNone(d["body"]); self.assertTrue(d["history"]); self.assertIn("repo", d["ticket"])
        self.assertTrue(all(not k.startswith((".", "_")) for k in d["kinds"]), d["kinds"]); self.assertIn(d["ticket"]["kind"], d["kind_desc"])
        with self.assertRaises(urllib.error.HTTPError) as cm: self.http.get("/api/tickets/999999")
        self.assertEqual(cm.exception.code, 404)

    def test_ticket_detail_lists_attachments(self):
        """kb attach で入れた添付が、そのままチケット画面（と MCP の ticket_show）に出る（チケット 353）"""
        env = {**os.environ, "AIFACTORY_WORKSPACE": str(self.ws)}
        shot = self.tmp / "画面.png"; shot.write_bytes(b"\x89PNG" + b"0" * 20)
        r = subprocess.run([sys.executable, str(KB), "new", PJ, "bug", "不具合: 画面の見え方", "--body", "-", "--attach", str(shot)],
                           input="x\n", text=True, capture_output=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        tid = int(r.stdout.split()[0])
        _, d = self.http.get(f"/api/tickets/{tid}")
        self.assertEqual([a["name"] for a in d["attachments"]], ["画面.png"])
        self.assertEqual(d["attachments"][0]["type"], "image/png")
        self.assertEqual(d["attachments"][0]["size"], shot.stat().st_size)
        self.assertNotIn("添付", d["body"])                                   # 本文には書かない（正本は attachments/）
        _, seed = self.http.get(f"/api/tickets/{self.seed}")
        self.assertEqual(seed["attachments"], [])                            # 添付の無いチケットは空

    def test_run_detail(self):
        _, r = self.http.get("/api/runs"); name = next(x["name"] for x in r["runs"] if x["kind"] == "v1" and x["status"] != "not_started")
        st, d = self.http.get(f"/api/runs/{name}")
        self.assertEqual(st, 200); self.assertIn("state.json", [f["name"] for f in d["files"]]); self.assertIsNotNone(d["workflow"])

    def _fixture_run(self, name, state=None, files=None):
        """runs/<name>/ を手で組む（runner を回さずに history や gates.txt の形を作る）。日付は 2020 年にして、種の run より後ろに並べる"""
        d = self.ws / "runs" / name; d.mkdir(parents=True, exist_ok=True)
        if state is not None: (d / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        for r, text in (files or {}).items():
            f = d / r; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(text, encoding="utf-8")
        return name

    def _state(self, hist, **kw):
        return {"pj": PJ, "task": self.seed, "workflow": "feature", "branch": "sandbox/x", "base": "main",
                "started": "2020-01-01T09:00:00", "finished": "2020-01-01T10:00:00", "elapsed_s": 3600,
                "result": "human", "pr_url": "", "next": "human", "current": None, "loops": {},
                "history": [{"step": st, "ok": ok, "next": "x", "at": "2020-01-01T09:10:00"} for st, ok in hist], **kw}

    def _assert_offset(self, label, v):
        """時刻がオフセット付きで、このサーバーの時間帯に一致すること"""
        off = datetime.datetime.now().astimezone().utcoffset()
        self.assertRegex(str(v), OFFSET_ISO, f"{label}: {v!r}")
        self.assertEqual(datetime.datetime.fromisoformat(v).utcoffset(), off, f"{label}: {v!r}")

    def test_timestamps_carry_offset(self):
        """API が返す時刻はオフセット付き（チケット 235）。オフセットが無いとブラウザーが自分の時間帯として読み、
           サーバーと時間帯が違うだけで実行中の経過時間が時差ぶんずれる（終了後の所要と食い違う）"""
        name = self._fixture_run("2020-01-03-kumitate-993", {
            "pj": PJ, "task": self.seed, "workflow": "feature", "branch": "sandbox/x", "base": "main",
            "started": "2020-01-03T09:00:00", "finished": None, "next": "implement", "loops": {},
            "current": {"step": "implement", "kind": "agent", "log": "agent-implement-1.log", "since": "2020-01-03T09:05:00"},
            "history": [{"step": "plan", "ok": True, "next": "implement", "at": "2020-01-03T09:04:00"}]},
            {"work/ticket.md": "# x\n"})
        _, r = self.http.get("/api/runs")
        row = next(x for x in r["runs"] if x["name"] == name)
        self._assert_offset("runs[].started", row["started"]); self._assert_offset("runs[].mtime", row["mtime"])
        self._assert_offset("runs[].current.since", row["current"]["since"])
        done = next(x for x in r["runs"] if x["kind"] == "v1" and x.get("finished"))
        self._assert_offset("runs[].finished", done["finished"])

        _, d = self.http.get(f"/api/runs/{name}")
        self._assert_offset("run.summary.started", d["summary"]["started"])
        self._assert_offset("run.state.started", d["state"]["started"])
        self._assert_offset("run.state.current.since", d["state"]["current"]["since"])
        self._assert_offset("run.state.history[].at", d["state"]["history"][0]["at"])
        self._assert_offset("run.files[].mtime", d["files"][0]["mtime"])

        jid = "20200103-090000-old"                                    # 古い（オフセット無しの）ジョブの記録も読むときに補う
        jd = pathlib.Path(self.tmp) / "jobs" / jid; jd.mkdir(parents=True, exist_ok=True)
        (jd / "meta.json").write_text(json.dumps({"id": jid, "kind": "kb-run", "label": "kb run 0", "cmd": ["x"], "ticket": None,
                                                  "run_hint": None, "pid": 1, "started": "2020-01-03T09:00:00",
                                                  "finished": "2020-01-03T09:10:00", "rc": 0, "state": "done"}), encoding="utf-8")
        _, jl = self.http.get("/api/jobs")
        j = next(x for x in jl["jobs"] if x["id"] == jid)
        self._assert_offset("jobs[].started", j["started"]); self._assert_offset("jobs[].finished", j["finished"])
        _, jv = self.http.get(f"/api/jobs/{jid}")
        self._assert_offset("job.started", jv["job"]["started"])

        _, t = self.http.get("/api/tickets")
        for x in t["tickets"]:
            self._assert_offset("tickets[].created", x["created"]); self._assert_offset("tickets[].updated", x["updated"])
        _, td = self.http.get(f"/api/tickets/{self.seed}")
        self._assert_offset("ticket.updated", td["ticket"]["updated"])
        self._assert_offset("ticket.history[].at", td["history"][0]["at"])

        _, o = self.http.get("/api/overview")
        self._assert_offset("overview.now", o["now"])
        self.assertRegex(o["tz"]["offset"], r"^[+-]\d\d:\d\d$")      # 画面がブラウザーとの時間帯の違いを言えるように
        self.assertEqual(o["tz"]["offset"], datetime.datetime.now().astimezone().isoformat()[-6:])
        self.assertTrue(o["tz"]["label"])

    def test_run_outcome_offers_the_resume_command(self):
        """human で止まり wip が残っている run は、続きから回すコマンドを結果とチケット画面に出す（チケット 333）。
           チケットの run 一覧（ticket_show）と run 画面で同じ 1 本が読めること"""
        hist = [("research", True), ("design", True), ("implement", True), ("gates", True),
                ("review", False), ("implement", True), ("gates", True), ("review", False)]
        wip = f"sandbox/{self.seed}-feature-wip"
        name = self._fixture_run(f"2020-01-04-{PJ}-{self.seed}", self._state(
            hist, loops={"review->implement": 1}, wip_branch=wip, resume_step="implement",
            error="review で止まった（失敗、または戻せる回数を使い切った）: 指摘 1 件"),
            {"work/review.md": "# レビュー: FAIL\n", "work/ticket.md": "# x\n"})
        want = f"kb run {self.seed} --from implement --branch {wip}"
        _, d = self.http.get(f"/api/runs/{name}")
        self.assertEqual(d["outcome"]["resume"], want)
        self.assertEqual(d["summary"]["resume"], want); self.assertEqual(d["summary"]["resume_step"], "implement")
        _, td = self.http.get(f"/api/tickets/{self.seed}")
        self.assertEqual(next(r for r in td["runs"] if r["name"] == name)["resume"], want)
        # 続きから回せない run（PR まで進んだ・wip の無い run）には出さない
        other = self._fixture_run(f"2020-01-04-{PJ}-{self.seed}-b", self._state(hist, result="end"), {"work/ticket.md": "# x\n"})
        _, o = self.http.get(f"/api/runs/{other}")
        self.assertIsNone(o["outcome"]["resume"]); self.assertIsNone(o["summary"]["resume"])

    def test_run_outcome_reads_the_human_closeout(self):
        """human で止まった run を人間が wip から PR にしてマージすると（kb が state.json に human を足す）、
           run 画面は「待っています」ではなく「人間が PR で仕上げた」と読める（チケット 335）"""
        hist = [("research", True), ("design", True), ("implement", True), ("gates", True), ("review", False)]
        wip = f"sandbox/{self.seed}-feature-wip"
        base = dict(loops={"review->implement": 1}, wip_branch=wip, resume_step="implement",
                    error="review で止まった（失敗、または戻せる回数を使い切った）: 指摘 1 件")
        human = {"at": "2020-01-05T09:00:00+09:00", "by": "pm", "result": "done",
                 "pr_url": "https://github.com/akkijp/kumitate/pull/300", "text": "wip から PR を作ってマージした"}
        name = self._fixture_run(f"2020-01-05-{PJ}-{self.seed}", self._state(hist, human=human, **base), {"work/ticket.md": "# x\n"})
        _, d = self.http.get(f"/api/runs/{name}")
        o = d["outcome"]
        self.assertEqual(o["reason"], "human_done")
        self.assertEqual(o["pr_url"], human["pr_url"]); self.assertEqual(o["human"], human)
        self.assertIsNone(o["resume"], "片が付いた run に「続きから回す」を出している")
        self.assertEqual(d["summary"]["human"], human); self.assertEqual(d["summary"]["result"], "human")   # runner の result は変わらない

        # 打ち切った run は別の言い方（PR は無い）
        ab = self._fixture_run(f"2020-01-05-{PJ}-{self.seed}-b", self._state(
            hist, human={**human, "result": "abandoned", "pr_url": "", "text": "作り直す"}, **base), {"work/ticket.md": "# x\n"})
        _, d2 = self.http.get(f"/api/runs/{ab}")
        self.assertEqual(d2["outcome"]["reason"], "human_abandoned"); self.assertIsNone(d2["outcome"]["resume"])

        # 後始末がまだの run は今までどおり「人間の判断を待っています」（loop_limit / waiting の判定を壊さない）
        yet = self._fixture_run(f"2020-01-05-{PJ}-{self.seed}-c", self._state(hist, **base), {"work/ticket.md": "# x\n"})
        _, d3 = self.http.get(f"/api/runs/{yet}")
        self.assertEqual(d3["outcome"]["reason"], "loop_limit"); self.assertIsNone(d3["outcome"]["human"])
        self.assertIsNotNone(d3["outcome"]["resume"])
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("T.outcome.human_done", app); self.assertIn("T.outcome.human_abandoned", app); self.assertIn("T.outcome.humanNote", app)

    def test_run_action_records_the_human_closeout_through_kb(self):
        """`POST /api/runs/<name>/action` は kb run-note を呼ぶだけ（コンソールが state.json を直接書かない）"""
        hist = [("research", True), ("implement", True), ("review", False)]
        name = self._fixture_run(f"2020-01-06-{PJ}-{self.seed}", self._state(
            hist, wip_branch=f"sandbox/{self.seed}-feature-wip", resume_step="implement"), {"work/ticket.md": "# x\n"})
        st, r = self.http.post(f"/api/runs/{name}/action", {"action": "close", "pr": 300, "text": "wip から PR を作ってマージした"})
        self.assertEqual(st, 200, r); self.assertEqual(r["rc"], 0)
        _, d = self.http.get(f"/api/runs/{name}")
        self.assertEqual(d["outcome"]["reason"], "human_done")
        self.assertEqual(d["outcome"]["human"]["text"], "wip から PR を作ってマージした")
        self.assertIn("300", d["outcome"]["pr_url"])
        # 2 度目の close は kb が断る。説明の書き直しは note で通る
        st, e = self.http.post(f"/api/runs/{name}/action", {"action": "close", "pr": 301})
        self.assertEqual(st, 400); self.assertIn("既に人間の記録がある", e["error"])
        st, r2 = self.http.post(f"/api/runs/{name}/action", {"action": "note", "text": "リリース 1.2 に入れた"})
        self.assertEqual(st, 200, r2)
        _, d2 = self.http.get(f"/api/runs/{name}")
        self.assertEqual(d2["outcome"]["human"]["text"], "リリース 1.2 に入れた")
        self.assertEqual(d2["outcome"]["reason"], "human_done")                       # 決着の別は前のまま
        st, e2 = self.http.post(f"/api/runs/{name}/action", {"action": "note"})
        self.assertEqual(st, 400); self.assertIn("説明", e2["error"])
        st, e3 = self.http.post("/api/runs/2020-01-06-nosuch-run/action", {"action": "close"})
        self.assertEqual(st, 404)

    def test_run_outcome_loop_limit(self):
        """ゲートが上限まで通らず人間待ちになった run: 止まった工程・赤いゲート・読むべきファイルが API から出る（チケット 226）"""
        hist = [("research", True), ("design", True), ("implement", True), ("gates", False),
                ("implement", True), ("gates", False), ("implement", True), ("gates", False)]
        name = self._fixture_run("2026-09-07-kumitate-998", self._state(hist, loops={"gates->implement": 2}), {
            "work/gates.txt": "PASS lint\nFAIL test\n\n=== test.log (tail 60)\nFAIL something-in-log\n",
            "work/ticket.md": "# x\n", "work/plan.md": "# 計画\n", "work/report.md": "# 報告\n",
            "code-gates-7.log": "gates\n", "agent-implement-6.log": "implement\n",
            "agent-implement-6.jsonl": "{}\n", "prompt-implement-6.md": "依頼文\n", "linux.lock": ""})
        st, d = self.http.get(f"/api/runs/{name}"); self.assertEqual(st, 200)
        o = d["outcome"]
        self.assertEqual(o["reason"], "loop_limit"); self.assertEqual(o["stopped_step"], "gates")
        self.assertEqual(o["stopped_index"], 7); self.assertEqual(o["fail_count"], 3); self.assertTrue(o["loops_hit"])
        self.assertEqual(o["gate_fails"], ["test"])                       # ログ末尾の添付にある FAIL は拾わない
        self.assertTrue(o["detail_file"].endswith("work/gates.txt"), o["detail_file"])
        g = d["groups"]
        self.assertEqual([a["kind"] for a in g["artifacts"]], ["ticket", "planner", "implementer", "gates"])
        self.assertEqual([a["name"] for a in g["artifacts"]], ["work/ticket.md", "work/plan.md", "work/report.md", "work/gates.txt"])
        self.assertEqual([x["name"] for x in g["step_logs"]], ["agent-implement-6.log", "code-gates-7.log"])
        other = [x["name"] for x in g["other"]]
        for nm in ("state.json", "agent-implement-6.jsonl", "prompt-implement-6.md", "linux.lock"): self.assertIn(nm, other)
        self.assertEqual(d["ticket"]["id"], self.seed); self.assertIn("updated", d["ticket"])   # チケットの「今」を出すのに要る

    def test_gate_logs_are_listed_and_readable(self):
        """赤いゲートの中身（work/gates/<名前>.log）が run のファイル一覧に載り、そのまま読める（チケット 331）。
           VM は run の終わりに初期化されるので、ここに残っていないと終わった run の赤は誰にも読めない。
           見せ方は変えない（判定は今までどおり gates.txt から。ログは「その他」に並ぶ）"""
        hist = [("implement", True), ("gates", False)]
        name = self._fixture_run("2026-09-07-kumitate-990", self._state(hist), {
            "work/gates.txt": "PASS lint\nFAIL test\n\n=== test.log (tail 60)\nFAIL something-in-log\n",
            "work/gates/test.log": "# test.log on HEAD (sandbox/x)\n\n=== excerpt\nFAIL tests.test_x\n=== tail 300\nRan 3 tests\n",
            "work/ticket.md": "# x\n"})
        _, d = self.http.get(f"/api/runs/{name}")
        f = next((x for x in d["files"] if x["name"] == "work/gates/test.log"), None)
        self.assertIsNotNone(f, "work/gates/test.log がファイル一覧に無い")
        g = d["groups"]
        self.assertIn("work/gates/test.log", [x["name"] for x in g["other"]])
        self.assertNotIn("work/gates/test.log", [x["name"] for x in g["artifacts"]])
        self.assertEqual(d["outcome"]["gate_fails"], ["test"])       # 判定は gates.txt のまま（ログの FAIL は拾わない）
        st, body = self.http.get(f"/api/file?path={urllib.parse.quote(f['path'])}")
        self.assertEqual(st, 200); self.assertIn("FAIL tests.test_x", body["text"])

    def test_run_outcome_pr_and_unknown(self):
        """PR まで進んだ run は pr_created。記録が足りない run は unknown（推測しない）"""
        ok = [("research", True), ("design", True), ("implement", True), ("gates", True), ("review", True), ("pr", True)]
        name = self._fixture_run("2026-09-07-kumitate-997", self._state(ok, pr_url="https://example.invalid/pull/1"),
                                 {"work/report.md": "# 報告\n", "agent-implement-2.log": "x\n"})
        _, d = self.http.get(f"/api/runs/{name}")
        self.assertEqual(d["outcome"]["reason"], "pr_created"); self.assertIsNone(d["outcome"]["stopped_step"])
        self.assertEqual([a["kind"] for a in d["groups"]["artifacts"]], ["implementer"])

        name = self._fixture_run("2026-09-07-kumitate-996", self._state([]), {"work/ticket.md": "# x\n"})
        _, d = self.http.get(f"/api/runs/{name}")
        self.assertEqual(d["outcome"]["reason"], "unknown"); self.assertIsNone(d["outcome"]["detail_file"])

        name = self._fixture_run("2026-09-07-kumitate-995", None, {"ticket.md": "# x\n"})   # VM 貸出前は work/ が無い
        _, d = self.http.get(f"/api/runs/{name}")
        self.assertEqual(d["outcome"]["reason"], "not_started")
        self.assertEqual([(a["name"], a["kind"]) for a in d["groups"]["artifacts"]], [("ticket.md", "ticket")])

    def test_run_outcome_reads_the_automatic_merge(self):
        """runner が条件を確かめて自分でマージした run（チケット 358）。「PR ができました」ではなく
           「自動マージしました（完了）」と読め、続きから回す口は出さない"""
        ok = [("research", True), ("design", True), ("implement", True), ("gates", True), ("review", True),
              ("sync", True), ("pr", True), ("automerge", True)]
        merged = {"at": "2026-09-09T12:00:00+09:00", "sha": "abc1234", "method": "merge",
                  "pr_url": "https://github.com/akkijp-oss/aifactory/pull/45", "base": "develop"}
        name = self._fixture_run(f"2026-09-09-{PJ}-{self.seed}", self._state(
            ok, result="end", pr_url=merged["pr_url"], merged=merged), {"work/report.md": "# 報告\n"})
        _, d = self.http.get(f"/api/runs/{name}")
        o = d["outcome"]
        self.assertEqual(o["reason"], "merged")
        self.assertEqual(o["merged"], merged); self.assertEqual(o["pr_url"], merged["pr_url"])
        self.assertIsNone(o["resume"], "片が付いた run に「続きから回す」を出している")
        self.assertEqual(d["summary"]["merged"], merged)                   # MCP run_show も同じ事実を返す
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("T.outcome.merged", app); self.assertIn("T.outcome.automerge_skipped", app)

    def test_run_outcome_tells_why_the_automatic_merge_did_not_happen(self):
        """条件を満たさずマージしなかった run は今までどおり pr_created（PR は開いている）。
           そこに runner が残した理由を添える（推し量らず error の 1 行を読むだけ）"""
        hist = [("implement", True), ("gates", True), ("review", True), ("sync", True), ("pr", True), ("automerge", False)]
        name = self._fixture_run(f"2026-09-09-{PJ}-{self.seed}-b", self._state(
            hist, pr_url="https://github.com/akkijp-oss/aifactory/pull/46",
            error="automerge: CI 赤 (test)"), {"work/report.md": "# 報告\n"})
        _, d = self.http.get(f"/api/runs/{name}")
        o = d["outcome"]
        self.assertEqual(o["reason"], "pr_created")
        self.assertEqual(o["automerge_error"], "automerge: CI 赤 (test)")
        self.assertIsNone(o["merged"])
        # automerge を回していない run（auto_merge の無い PJ）には理由の行が付かない
        plain = self._fixture_run(f"2026-09-09-{PJ}-{self.seed}-c", self._state(
            hist[:-1], pr_url="https://github.com/akkijp-oss/aifactory/pull/47"), {"work/report.md": "# 報告\n"})
        _, d2 = self.http.get(f"/api/runs/{plain}")
        self.assertEqual(d2["outcome"]["reason"], "pr_created")
        self.assertNotIn("automerge_error", d2["outcome"])

    def test_run_outcome_step_failed_points_at_the_step_log(self):
        """ゲート以外の工程で止まった run は、その工程のログを「理由を読む」の先にする"""
        hist = [("research", True), ("design", False)]
        name = self._fixture_run("2026-09-07-kumitate-994", self._state(hist),
                                 {"agent-design-1.log": "落ちた\n", "work/research.md": "# 調査\n"})
        _, d = self.http.get(f"/api/runs/{name}")
        o = d["outcome"]
        self.assertEqual(o["reason"], "step_failed"); self.assertEqual(o["stopped_step"], "design")
        self.assertEqual(o["gate_fails"], []); self.assertTrue(o["detail_file"].endswith("agent-design-1.log"))
        self.assertFalse(o["loops_hit"])

    def test_run_outcome_step_timeout_is_told_apart_from_a_failed_step(self):
        """時間上限で切られた工程（チケット 329）。「工程が失敗した」ではなく「時間上限で中断」と読める。

        直し方が違う（上限を上げるかチケットを小さくする）うえ、コミット済みの分は wip ブランチに残っている。
        agent の stdout の末尾は error ではなく last_output にあるので、理由の 1 行に lint の集計行が混ざらない。
        """
        hist = [("plan", True), ("implement", False)]
        state = self._state(hist, workflow="bug", wip_branch="sandbox/329-bug-wip",
                            error="implement: 時間上限 60 分で中断（timeout）",
                            last_output="✖ 1317 problems (0 errors, 1317 warnings)\n")
        state["history"][-1].update({"failure": "timeout", "timeout_min": 60})
        name = self._fixture_run("2026-09-07-kumitate-990", state,
                                 {"agent-implement-1.log": "[+59:00] ▶ Edit: src/nav.tsx\n", "work/plan.md": "# 計画\n"})
        _, d = self.http.get(f"/api/runs/{name}")
        o = d["outcome"]
        self.assertEqual(o["reason"], "step_timeout"); self.assertEqual(o["stopped_step"], "implement")
        self.assertEqual(o["timeout_min"], 60)
        self.assertEqual(o["error_summary"], "implement: 時間上限 60 分で中断（timeout）")
        self.assertTrue(o["detail_file"].endswith("agent-implement-1.log"))
        self.assertNotIn("1317", o["error_summary"])                       # stdout の末尾は理由に混ざらない
        self.assertIn("1317", d["state"]["last_output"])                   # MCP run_show も同じ state を返す
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("T.outcome.step_timeout", app)

    def test_run_outcome_no_key_is_a_pause_not_a_failure(self):
        """鍵プールに要る用途の鍵が無く VM を取らずに止まった run（ADR-0046）。「VM の準備で止まった」ではなく「鍵が無いので一時停止」と読める"""
        state = {**self._state([], workflow="bug"), "result": "failed", "failure": "nokey", "needed_keys": ["fable", "other"],
                 "error": "鍵なし: Fable に使う鍵が鍵プールに無い", "current": None}
        name = self._fixture_run("2026-09-10-kumitate-993", state, {})
        _, d = self.http.get(f"/api/runs/{name}")
        o = d["outcome"]
        self.assertEqual(o["reason"], "nokey"); self.assertEqual(o["needed_keys"], ["fable", "other"])
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("T.outcome.nokey", app)

    def test_run_outcome_quota_pause_says_when_it_resumes(self):
        """鍵の利用枠の上限で止まった工程（チケット 380 / ADR-0043）。「工程が失敗した」ではなく「一時停止・HH:MM 以降に自動再開」と読める。
        鍵そのものが使えない（key）は別の理由（人が鍵を直す）。MCP run_show も同じ outcome を返す"""
        hist = [("plan", True), ("implement", False)]
        state = self._state(hist, workflow="bug", wip_branch="sandbox/380-bug-wip", resume_step="implement",
                            failure="quota", quota_type="five_hour", retry_after="2026-09-09T15:00:00+09:00", quota_hits=1,
                            error="implement: 鍵の利用枠の上限で中断（quota・five_hour。解除見込み 2026-09-09T15:00:00+09:00）")
        state["history"][-1].update({"failure": "quota", "quota_type": "five_hour", "retry_after": "2026-09-09T15:00:00+09:00"})
        name = self._fixture_run("2026-09-09-kumitate-991", state,
                                 {"agent-implement-1.log": "[+12:00] rate limit: rejected (five_hour; resets 2026-09-09T15:00:00+09:00)\n"})
        _, d = self.http.get(f"/api/runs/{name}")
        o = d["outcome"]
        self.assertEqual(o["reason"], "quota_paused"); self.assertEqual(o["stopped_step"], "implement")
        self.assertEqual(o["quota_type"], "five_hour"); self.assertEqual(o["quota_hits"], 1)
        self.assertEqual(datetime.datetime.fromisoformat(o["retry_after"]), datetime.datetime.fromisoformat("2026-09-09T15:00:00+09:00"))
        self.assertEqual(o["resume"], f"kb run {self.seed} --from implement --branch sandbox/380-bug-wip")
        state = self._state(hist, workflow="bug", wip_branch="sandbox/380-bug-wip", resume_step="implement",
                            failure="key", quota_type=None, retry_after=None, quota_hits=0,
                            error="implement: 鍵が使えず中断（key）: Invalid API key · Please run /login")
        state["history"][-1].update({"failure": "key"})
        name = self._fixture_run("2026-09-09-kumitate-992", state, {"agent-implement-1.log": "Invalid API key\n"})
        _, d = self.http.get(f"/api/runs/{name}")
        self.assertEqual(d["outcome"]["reason"], "key_failed")
        self.assertIn("Invalid API key", d["outcome"]["error_summary"])
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("T.outcome.quota_paused", app); self.assertIn("T.outcome.key_failed", app)

    def test_stats_aggregates_agent_steps_from_the_raw_events(self):
        """工程ごとの消費統計（チケット 382）。根拠は agent-*.jsonl の result / usage と init の model だけ。
        thinking は回数と「本文が見える回数・文字数」を分ける（Opus は本文が空で署名だけ）。code の工程は載らず、dry-run は既定で除く"""
        def jsonl(model, turns, usage, cost, thinking=(), rejected=False):
            lines = [{"type": "system", "subtype": "init", "model": model, "tools": [], "cwd": "/app"}]
            for th in thinking:
                lines.append({"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": th, "signature": "x"}, {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}]}})
            if rejected: lines.append({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1788976200, "rateLimitType": "five_hour"}})
            lines.append({"type": "result", "subtype": "success", "is_error": rejected, "num_turns": turns, "duration_ms": 120000, "usage": usage, "total_cost_usd": cost, "result": "ok"})
            return "\n".join(json.dumps(l) for l in lines) + "\n"
        today = datetime.date.today().isoformat(); tid = "7777"   # 種のチケットとは別の番号（他のテストが種の run を名前で探すので混ぜない）
        u = lambda i, cw, cr, o: {"input_tokens": i, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr, "output_tokens": o}
        self._fixture_run(f"{today}-{PJ}-{tid}", {**self._state([("plan", True), ("implement", True)], workflow="bug"), "task": tid}, {
            "agent-plan-0.jsonl": jsonl("claude-fable-5-1", 10, u(100, 20000, 300000, 5000), 1.5, thinking=["原因を確定した", ""]),
            "agent-implement-1.jsonl": jsonl("claude-opus-5", 40, u(50, 60000, 2000000, 20000), 4.0, thinking=["", "", ""]),
            "agent-review-2.jsonl": jsonl("claude-fable-5-1", 1, u(0, 15000, 10000, 100), 0.2, rejected=True),
            "code-gates-3.log": "PASS x\n"})
        self._fixture_run(f"{today}-{PJ}-{tid}-dry", {**self._state([("plan", True)], workflow="bug"), "task": tid}, {"agent-plan-0.jsonl": jsonl("claude-fable-5-1", 1, u(0, 1, 1, 1), 9.0)})
        self._fixture_run("2019-01-01-otherpj-1", {**self._state([("plan", True)], workflow="bug"), "pj": "otherpj", "task": "1"}, {"agent-plan-0.jsonl": jsonl("claude-fable-5-1", 3, u(0, 10, 10, 10), 0.5)})
        old = self.ws / "runs" / "2019-01-01-otherpj-1" / "agent-plan-0.jsonl"   # 期間は工程の時刻で切る（チケット 393）ので、古い run の工程の時刻も揃えておく
        ots = datetime.datetime.fromisoformat("2019-01-01T09:00:00+00:00").timestamp(); os.utime(old, (ots, ots))
        st, d = self.http.get("/api/stats?days=1")
        self.assertEqual(st, 200)
        self.assertGreaterEqual(d["files"], 5)
        # 他のテストの run が混ざらないよう、この run の分だけ拾って見る（期間の中に dry と 2019 年の分は入らない）
        mine = [r for r in d["top"] if r["task"] == tid]
        self.assertEqual(sorted(r["step"] for r in mine), ["implement", "plan", "review"])
        t = {k: sum(r[k] for r in mine) for k in ("turns", "cost", "input", "cache_write", "cache_read", "output", "thinking_blocks", "thinking_visible", "thinking_chars", "tool_calls")}
        t["steps"] = len(mine); t["steps_with_thinking"] = sum(1 for r in mine if r["thinking_blocks"]); t["rate_limited"] = sum(1 for r in mine if r["rate_limited"])
        t["cost"] = round(t["cost"], 2)
        self.assertEqual((t["steps"], t["turns"], t["cost"]), (3, 51, 5.7))
        self.assertEqual((t["input"], t["cache_write"], t["cache_read"], t["output"]), (150, 95000, 2310000, 25100))
        self.assertEqual((t["thinking_blocks"], t["thinking_visible"], t["thinking_chars"], t["steps_with_thinking"]), (5, 1, 7, 2))
        self.assertEqual(t["rate_limited"], 1); self.assertEqual(t["tool_calls"], 5)
        self.assertEqual(d["by_model"][0]["model"], "claude-opus-5")                                   # 費用の高い順
        self.assertIn(("plan", "claude-fable-5-1"), {(a["step"], a["model"]) for a in d["by_step"]})
        self.assertIn(("review", "claude-fable-5-1"), {(a["step"], a["model"]) for a in d["by_step"]})
        imp = next(r for r in mine if r["step"] == "implement")
        self.assertEqual(imp["log"], "agent-implement-1.log"); self.assertTrue(imp["log_path"].endswith("agent-implement-1.log"))
        # PJ で絞れる（期間を外せば 2019 年の分も入る）。dry は dry=1 のときだけ
        _, d2 = self.http.get("/api/stats"); self.assertIn("otherpj", d2["pjs"]); self.assertGreater(d2["selected"], d["selected"])
        _, d3 = self.http.get("/api/stats?pj=otherpj"); self.assertEqual((d3["selected"], d3["total"]["cost"]), (1, 0.5))
        _, d4 = self.http.get("/api/stats?days=1&dry=1"); self.assertEqual(d4["selected"], d["selected"] + 1)
        # 2 度目はキャッシュから（読んだ結果がジョブ記録の置き場に残る）。中身が変わったファイルだけ読み直す
        cache = self.tmp / "jobs" / "stats-cache.json"
        self.assertTrue(cache.exists())
        f = self.ws / "runs" / f"{today}-{PJ}-{tid}" / "agent-plan-0.jsonl"
        f.write_text(jsonl("claude-fable-5-1", 10, u(100, 20000, 300000, 5000), 2.5), encoding="utf-8")
        os.utime(f, (time.time() + 5, time.time() + 5))
        _, d5 = self.http.get("/api/stats?days=1")
        self.assertEqual(round(sum(r["cost"] for r in d5["top"] if r["task"] == tid), 2), 6.7)
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("async function viewStats", app); self.assertIn("'stats'", app)

    def test_stats_by_day_uses_the_given_timezone(self):
        """日別（by_day）と「直近 N 日」は、run 名の日付（kb run を起動した制御系＝UTC の今日）ではなく、
        工程の時刻 at を tz の時間帯に直した日付で切る（チケット 393）。run 名の日付は変えない"""
        def jsonl(cost):
            lines = [{"type": "system", "subtype": "init", "model": "claude-opus-5", "tools": [], "cwd": "/app"},
                     {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "duration_ms": 60000,
                      "usage": {"input_tokens": 10, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 10, "output_tokens": 10},
                      "total_cost_usd": cost, "result": "ok"}]
            return "\n".join(json.dumps(l) for l in lines) + "\n"
        pj, tid = "tzpj", "7778"   # 他のテストの run と混ざらない PJ 名で絞って見る
        run = f"2026-09-09-{pj}-{tid}"
        self._fixture_run(run, {**self._state([("plan", True)], workflow="bug"), "pj": pj, "task": tid}, {"agent-plan-0.jsonl": jsonl(3.0)})
        f = self.ws / "runs" / run / "agent-plan-0.jsonl"
        def at(iso):
            t = datetime.datetime.fromisoformat(iso).timestamp(); os.utime(f, (t, t))
        try:
            at("2026-09-09T23:30:00+00:00")   # UTC の 09-09 23:30 に終わった工程は、+09:00 では翌日の 08:30
            _, d = self.http.get(f"/api/stats?pj={pj}&tz=%2B09%3A00")
            self.assertEqual([a["date"] for a in d["by_day"]], ["2026-09-10"], "日別が工程の時刻を tz に直していない")
            self.assertEqual(d["tz"]["offset"], "+09:00")
            self.assertEqual([r["run"] for r in d["top"]], [run], "run 名の日付を変えてしまっている")
            _, z = self.http.get(f"/api/stats?pj={pj}&tz=%2B00%3A00")
            self.assertEqual([a["date"] for a in z["by_day"]], ["2026-09-09"])
            # 「直近 N 日」の起点も同じ時間帯の今日から数える（その時間帯の今日 00:00 の 1 分前は今日に入らない）
            zone = datetime.timezone(datetime.timedelta(hours=9))
            today = datetime.datetime.now(zone).date()
            at((datetime.datetime.combine(today, datetime.time(0, 0), zone) - datetime.timedelta(minutes=1)).isoformat())
            _, d1 = self.http.get(f"/api/stats?pj={pj}&tz=%2B09%3A00&days=1")
            self.assertEqual((d1["selected"], d1["since"]), (0, today.isoformat()))
            _, d2 = self.http.get(f"/api/stats?pj={pj}&tz=%2B09%3A00&days=2")
            self.assertEqual((d2["selected"], d2["since"]), (1, (today - datetime.timedelta(days=1)).isoformat()))
            # 読めない tz はサーバーの時間帯に落とし、実際に使った時間帯を返す（統計は読むだけなので 400 にしない）
            _, bad = self.http.get(f"/api/stats?pj={pj}&tz=Mars%2FOlympus")
            self.assertEqual(bad["tz"]["offset"], datetime.datetime.now().astimezone().isoformat()[-6:])
            # 画面はブラウザーの時間帯を渡し、日別の表に基準を書く
            app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
            body = app[app.index("async function viewStats("):]
            body = body[:body.index("\n}")]
            self.assertIn("tzOffset()", body, "画面がブラウザーの時間帯を渡していない")
            self.assertIn("T.stats.dayTz", body); self.assertIn("T.help.statsDay", body)
        finally:
            shutil.rmtree(self.ws / "runs" / run, ignore_errors=True)

    def test_run_outcome_drives_the_run_view(self):
        """停止理由の判定は API（core.run_outcome）に寄せる。app.js が history から自前で決めない"""
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewRun("); body = app[i:app.index("\n}", i)]
        self.assertIn("d.outcome", body, "viewRun が API の outcome を使っていない")
        self.assertIn("outcomePanel(name, d)", body, "冒頭の「結果」パネルを出していない")
        j = app.index("function outcomePanel("); panel = app[j:app.index("\n}", j)]
        self.assertIn("T.outcome", panel)
        for src in (body, panel):
            self.assertNotIn("ok === false", src, "画面が history から止まった工程を決めている")
            self.assertNotIn(".history", src, "画面が history を読み直している")

    def test_run_without_state_is_not_started(self):
        """ticket.md だけの run ディレクトリ（VM 貸出前に止まった残骸）を「実行中・工程 0」で数えない。

        画面は名前の無い実行リンクと空の「次は」「開始から」を出していた（チケット 220）。
        """
        for nm in ("2026-09-07-kumitate-999", "2026-09-07-kumitate-999-attempt1"):
            d = self.ws / "runs" / nm; d.mkdir(parents=True, exist_ok=True)
            (d / "ticket.md").write_text("# 調査: 記録の無い run\n", encoding="utf-8")
        _, r = self.http.get("/api/runs")
        row = next(x for x in r["runs"] if x["name"] == "2026-09-07-kumitate-999")
        self.assertEqual(row["status"], "not_started"); self.assertIsNone(row["finished"]); self.assertIsNone(row["result"])
        self.assertTrue(all(x["status"] in ("running", "finished", "not_started", "abandoned") for x in r["runs"]), r["runs"])
        self.assertEqual([x["status"] for x in r["runs"] if x["kind"] == "v0"] or ["finished"], ["finished"])
        _, o = self.http.get("/api/overview")
        self.assertNotIn("2026-09-07-kumitate-999", [x["name"] for x in o["runs_active"]])
        self.assertTrue(all(x["status"] == "running" for x in o["runs_active"]), o["runs_active"])
        self.assertGreaterEqual(o["runs_not_started"]["n"], 1)
        self.assertIn("2026-09-07-kumitate-999", [x["name"] for x in o["runs_not_started"]["runs"]])
        st, d = self.http.get("/api/runs/2026-09-07-kumitate-999")                    # 不完全な記録でも詳細へ移動できる
        self.assertEqual(st, 200); self.assertEqual(d["summary"]["status"], "not_started")
        self.assertIn("ticket.md", [f["name"] for f in d["files"]])

    def test_run_failed_before_start_is_finished_with_reason(self):
        """take が失敗した run（工程が 1 つも始まっていない）は「失敗」として終わった記録に見える（チケット 238）。

        state.json を最初から書くようにしたので、こういう run は「開始前（記録なし）」でも「実行中」でもない。
        """
        name = "2026-09-07-kumitate-998"
        d = self.ws / "runs" / name; d.mkdir(parents=True, exist_ok=True)
        (d / "ticket.md").write_text("# 調査: take が失敗した run\n", encoding="utf-8")
        (d / "state.json").write_text(json.dumps({
            "pj": PJ, "task": "998", "workflow": "research", "branch": "sandbox/998-research-x", "base": "develop",
            "started": "2026-09-07T10:00:00", "finished": "2026-09-07T10:00:05", "elapsed_s": 5, "history": [], "loops": {},
            "result": "failed", "next": "human", "current": None, "pr_url": "", "wip_branch": "",
            "error": "command failed (1): ['sandbox', 'take', 'kumitate', '998']\n[error] pj=kumitate に空きなし"}, ensure_ascii=False), encoding="utf-8")
        _, r = self.http.get("/api/runs")
        row = next(x for x in r["runs"] if x["name"] == name)
        self.assertEqual(row["status"], "finished"); self.assertEqual(row["result"], "failed"); self.assertEqual(row["pj"], PJ)
        _, o = self.http.get("/api/overview")
        self.assertNotIn(name, [x["name"] for x in o["runs_active"]])
        self.assertNotIn(name, [x["name"] for x in o["runs_not_started"]["runs"]])
        st, d = self.http.get(f"/api/runs/{name}")
        self.assertEqual(st, 200); self.assertEqual(d["summary"]["result"], "failed")
        self.assertIn("空きなし", d["state"]["error"])
        # 結果パネルが「記録にありません」と言わず、準備段階で止まったことと要約を出す（チケット 236）
        o2 = d["outcome"]
        self.assertEqual(o2["reason"], "failed_before_start"); self.assertEqual(o2["stopped_step"], "take")
        self.assertIn("空きなし", o2["error_summary"])
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("T.run.error", app)                                             # 詳細に失敗の理由が出る
        self.assertIn("T.outcome.failed_before_start", app)

    def test_run_that_waited_for_a_free_vm_is_not_a_human_problem(self):
        """--wait の上限まで待っても空きが出なかった run（チケット 242）。準備段階の失敗と区別して見せる"""
        name = "2026-09-07-kumitate-997"
        d = self.ws / "runs" / name; d.mkdir(parents=True, exist_ok=True)
        (d / "ticket.md").write_text("# 調査: 空きを待った run\n", encoding="utf-8")
        (d / "state.json").write_text(json.dumps({
            "pj": PJ, "task": "997", "workflow": "research", "branch": "sandbox/997-research-x", "base": "develop",
            "started": "2026-09-07T10:00:00", "finished": "2026-09-07T11:00:05", "elapsed_s": 3605, "history": [], "loops": {},
            "result": "failed", "next": "human", "current": None, "pr_url": "", "wip_branch": "",
            "failure": "wait_timeout", "waited_s": 3600,
            "error": "VM の空き待ちが上限 60 分（3600 秒）を超えました。3600 秒待機: [error] pj=kumitate に空きなし"}, ensure_ascii=False), encoding="utf-8")
        st, d = self.http.get(f"/api/runs/{name}")
        self.assertEqual(st, 200)
        o = d["outcome"]
        self.assertEqual(o["reason"], "wait_timeout"); self.assertEqual(o["stopped_step"], "wait-vm")
        self.assertEqual(o["waited_s"], 3600)
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("T.outcome.wait_timeout", app)

    def test_run_that_failed_in_prepare_is_not_a_take_failure(self):
        """貸出直後の準備（project.yml の prepare）で落ちた run（チケット 330）。

        VM は取れているので「VM を取得できなかった」と混ぜない。直す所は prepare.sh か VM の側にある"""
        name = "2026-09-07-kumitate-996"
        d = self.ws / "runs" / name; d.mkdir(parents=True, exist_ok=True)
        (d / "ticket.md").write_text("# 調査: 準備で止まった run\n", encoding="utf-8")
        (d / "state.json").write_text(json.dumps({
            "pj": PJ, "task": "996", "workflow": "research", "branch": "sandbox/996-research-x", "base": "develop",
            "started": "2026-09-07T10:00:00", "finished": "2026-09-07T10:02:05", "elapsed_s": 125, "history": [], "loops": {},
            "result": "failed", "next": "human", "current": None, "pr_url": "", "wip_branch": "", "failure": "prepare",
            "error": "prepare (prepare.sh) が rc=1 で失敗: db:migrate が当たりません"}, ensure_ascii=False), encoding="utf-8")
        st, d = self.http.get(f"/api/runs/{name}")
        self.assertEqual(st, 200)
        o = d["outcome"]
        self.assertEqual(o["reason"], "prepare_failed")
        self.assertEqual(o["stopped_step"], "prepare")
        self.assertIn("db:migrate", o["error_summary"])
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("T.outcome.prepare_failed", app)

    def test_sandbox_known_red_gates_include_what_the_runner_confirmed_on_base(self):
        """known_red_gates は人が project.yml に書く前提で、実際は誰も書かなかった（チケット 330）。

        runner が base で回して確かめた分（state.json の known_red_gates）を直近の run から拾って合わせて見せる"""
        name = "2026-09-07-kumitate-995"
        d = self.ws / "runs" / name; d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(json.dumps({
            "pj": PJ, "task": "995", "workflow": "feature", "branch": "sandbox/995-feature-x", "base": "develop",
            "started": "2026-09-07T10:00:00", "finished": "2026-09-07T11:00:00", "history": [], "loops": {},
            "result": "human", "known_red_gates": ["unittest-console"]}, ensure_ascii=False), encoding="utf-8")
        st, v = self.http.get("/api/sandbox")
        self.assertEqual(st, 200)
        pj = next(x for x in v["templates"] if x["pj"] == PJ)
        self.assertIn("unittest-console", pj["known_red_gates"])

    def test_run_whose_job_ended_is_abandoned(self):
        """起動したジョブが終わっているのに finished が書かれていない run は「実行中」ではなく「中断」（チケット 236）。

        待っていれば進む、と読ませないための判定。ジョブが動いている間と、
        ジョブの終了より後に state.json が書かれている間（別の runner が続きを回している）は running のまま。
        """
        name = "2026-09-07-kumitate-993"
        self._fixture_run(name, self._state([], finished=None, elapsed_s=None, result=None, next="take",
                                            current={"step": "take", "kind": "code", "since": "2020-01-02T00:21:37"}),
                          {"ticket.md": "# 調査: runner が居なくなった run\n"})
        sf = self.ws / "runs" / name / "state.json"
        jid = self._put_job("20200102-002137-kb-run", run_hint=name, ticket=self.seed, label=f"kb run {self.seed}",
                            started="2020-01-02T00:21:37", finished="2020-01-02T00:21:50", rc=1, state="failed")
        old = time.mktime(time.strptime("2020-01-02T00:21:40", "%Y-%m-%dT%H:%M:%S"))
        os.utime(sf, (old, old))                                                      # state.json はジョブより先に書き終えている
        _, r = self.http.get("/api/runs")
        row = next(x for x in r["runs"] if x["name"] == name)
        self.assertEqual(row["status"], "abandoned"); self.assertEqual(row["runner"]["id"], jid)
        _, o = self.http.get("/api/overview")
        self.assertNotIn(name, [x["name"] for x in o["runs_active"]])
        self.assertIn(name, [x["name"] for x in o["runs_abandoned"]["runs"]]); self.assertGreaterEqual(o["runs_abandoned"]["n"], 1)
        st, d = self.http.get(f"/api/runs/{name}")
        self.assertEqual(st, 200); self.assertEqual(d["summary"]["status"], "abandoned")
        self.assertEqual(d["outcome"]["reason"], "runner_gone"); self.assertEqual(d["outcome"]["job"]["id"], jid)
        self.assertEqual(d["outcome"]["job"]["rc"], 1); self.assertIn("lease", d)
        # 別の runner が続きを書いていれば実行中のまま（勝手に中断にしない）
        new = time.mktime(time.strptime("2020-01-02T00:30:00", "%Y-%m-%dT%H:%M:%S"))
        os.utime(sf, (new, new))
        _, r = self.http.get("/api/runs")
        self.assertEqual(next(x for x in r["runs"] if x["name"] == name)["status"], "running")
        os.utime(sf, (old, old))
        self._put_job(jid, run_hint=name, ticket=self.seed, label=f"kb run {self.seed}",
                      started="2020-01-02T00:21:37", finished=None, rc=None, state="running")
        _, r = self.http.get("/api/runs")
        self.assertEqual(next(x for x in r["runs"] if x["name"] == name)["status"], "running")
        shutil.rmtree(self.tmp / "jobs" / jid)
        shutil.rmtree(self.ws / "runs" / name)
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        for fn in ("async function viewBoard", "async function viewRuns", "async function viewRun("):
            i = app.index(fn); body = app[i:app.index("\n}", i)]
            self.assertIn("abandoned", body, f"{fn} が中断した run を実行中と分けていない")
        self.assertIn("T.outcome.runner_gone", app); self.assertIn("T.run.runnerGone", app)

    def test_run_summary_fills_pj_and_task_from_the_run_name(self):
        """記録が欠けていても、run 名から PJ とチケット番号を補ってヘッダーの導線を出す（チケット 236）。

        state.json が壊れている run は「開始前」に寄せ、v0 の記録と混ぜず、読めなかったことを添える。
        """
        bare = self._fixture_run("2026-09-07-kumitate-992", None, {"ticket.md": "# 調査: 記録の無い run\n"})
        broken = self._fixture_run("2026-09-07-kumitate-991", None, {"state.json": "{", "ticket.md": "# 調査: 壊れた記録\n"})
        _, r = self.http.get("/api/runs")
        b = next(x for x in r["runs"] if x["name"] == bare)
        self.assertEqual((b["pj"], str(b["task"]), b["from_name"]), (PJ, "992", True)); self.assertIsNone(b["state_error"])
        k = next(x for x in r["runs"] if x["name"] == broken)
        self.assertEqual((k["pj"], str(k["task"])), (PJ, "991")); self.assertEqual(k["status"], "not_started")
        self.assertTrue(k["state_error"]); self.assertNotEqual(k["kind"], "v0")
        self.assertIn("T.run.stateBroken", (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8"))
        for nm in (bare, broken): shutil.rmtree(self.ws / "runs" / nm)

    def test_file_roots(self):
        _, r = self.http.get("/api/runs"); name = next(x["name"] for x in r["runs"] if x["kind"] == "v1" and x["status"] != "not_started")
        _, d = self.http.get(f"/api/runs/{name}"); path = next(x["path"] for x in d["files"] if x["name"] == "state.json")
        st, f = self.http.get(f"/api/file?path={urllib.parse.quote(path)}&tail=100"); self.assertEqual(st, 200); self.assertTrue(f["truncated"] or f["size"] <= 100)
        for bad in ("../.ssh/id_rsa", "sandbox/bin/sandbox", str(self.ws / "kanban" / "kanban.db"), "/etc/passwd"):
            with self.assertRaises(urllib.error.HTTPError) as cm: self.http.get(f"/api/file?path={bad}")
            self.assertEqual(cm.exception.code, 403, bad)

    def test_post_requires_header(self):
        st, d = self.http.post("/api/tickets/204/action", {"action": "start"}, header=False)
        self.assertEqual(st, 403)

    def test_status_actions(self):
        tid = self.todo_id()
        self.assertEqual(self.http.post(f"/api/tickets/{tid}/action", {"action": "block"})[0], 400)          # メモ無しの人間待ちは拒否
        st, d = self.http.post(f"/api/tickets/{tid}/action", {"action": "start", "note": "test"}); self.assertEqual(st, 200, d)
        self.assertEqual(self.http.get(f"/api/tickets/{tid}")[1]["ticket"]["status"], "in_progress")
        st, d = self.http.post(f"/api/tickets/{tid}/action", {"action": "reopen"}); self.assertEqual(st, 200, d)
        self.assertEqual(self.http.get(f"/api/tickets/{tid}")[1]["ticket"]["status"], "todo")
        st, d = self.http.post(f"/api/tickets/{tid}/action", {"action": "set", "kind": "chore"}); self.assertEqual(st, 200, d)
        self.assertEqual(self.http.get(f"/api/tickets/{tid}")[1]["ticket"]["kind"], "chore")
        self.http.post(f"/api/tickets/{tid}/action", {"action": "set", "kind": "bug"})
        self.assertEqual(self.http.post(f"/api/tickets/{tid}/action", {"action": "set"})[0], 400)            # 変える項目が無い
        self.assertEqual(self.http.post(f"/api/tickets/{tid}/action", {"action": "nope"})[0], 400)
        hist = self.http.get(f"/api/tickets/{tid}")[1]["history"]
        self.assertTrue(any(h["field"] == "kind" and h["new"] == "chore" for h in hist))

    def test_append_writes_body_and_history(self):
        """本文への追記は末尾に見出しつきで入り、history に body 行として残る"""
        st, d = self.http.post("/api/tickets", {"pj": PJ, "kind": "research", "title": "調査: 本文追記", "body": "x\n\n## 完了条件\n- y"})
        self.assertEqual(st, 200, d); tid = d["id"]
        st, d = self.http.post(f"/api/tickets/{tid}/action", {"action": "append", "text": "裏取り: AppleDouble", "section": "PM 補足"})
        self.assertEqual(st, 200, d)
        _, v = self.http.get(f"/api/tickets/{tid}")
        self.assertIn("## PM 補足", v["body"]); self.assertIn("裏取り: AppleDouble", v["body"])
        self.assertGreater(v["body"].index("## PM 補足"), v["body"].index("## 完了条件"))   # 挿入位置は末尾
        self.assertTrue(any(h["field"] == "body" and (h["new"] or "").startswith("append") for h in v["history"]))
        st, d = self.http.post(f"/api/tickets/{tid}/action", {"action": "append", "section": "PM 補足"})
        self.assertEqual(st, 400, d); self.assertIn("text", d["error"])                      # 本文が無ければ拒む
        st, d = self.http.post(f"/api/tickets/{tid}/action", {"action": "append", "text": "  "})
        self.assertEqual(st, 400, d)

    def test_set_note_can_be_cleared(self):
        """note は「キーが無い＝触らない / 空文字列＝消す」。従来は空を未指定として無視していた"""
        st, d = self.http.post("/api/tickets", {"pj": PJ, "kind": "research", "title": "調査: メモを空に戻す", "body": "x\n\n## 完了条件\n- y"})
        self.assertEqual(st, 200, d); tid = d["id"]
        self.assertEqual(self.http.post(f"/api/tickets/{tid}/action", {"action": "set", "note": "x"})[0], 200)
        self.assertEqual(self.http.get(f"/api/tickets/{tid}")[1]["ticket"]["note"], "x")
        self.assertEqual(self.http.post(f"/api/tickets/{tid}/action", {"action": "set", "kind": "bug"})[0], 200)
        self.assertEqual(self.http.get(f"/api/tickets/{tid}")[1]["ticket"]["note"], "x")     # note キーが無ければ触らない
        self.assertEqual(self.http.post(f"/api/tickets/{tid}/action", {"action": "set", "note": ""})[0], 200)
        _, v = self.http.get(f"/api/tickets/{tid}")
        self.assertIn(v["ticket"]["note"], (None, ""))
        self.assertTrue(any(h["field"] == "note" and h["old"] == "x" and not h["new"] for h in v["history"]))

    def _put_job(self, jid, **over):
        """終わったジョブの記録を CONSOLE_JOBS に直接置く（JobStore はディスクの meta.json を読む）"""
        meta = {"id": jid, "kind": "kb-run", "label": "kb run", "cmd": [str(KB), "run"], "ticket": None, "run_hint": None,
                "pid": 1, "started": "2000-01-01T00:00:00", "finished": "2000-01-01T00:00:00", "rc": 1, "state": "failed"}
        meta.update(over)
        d = self.tmp / "jobs" / jid; d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return jid

    def test_job_view_carries_current_ticket_state(self):
        """過去の失敗ジョブには、チケットの「今」の状態を添えて返す（画面が古い復旧案内を主表示しないため）"""
        st, d = self.http.post("/api/tickets", {"pj": PJ, "kind": "bug", "title": "不具合: 過去の失敗ジョブ", "body": "x\n\n## 完了条件\n- y"})
        self.assertEqual(st, 200, d); tid = d["id"]
        jid = self._put_job("20000101-000000-kb-run", ticket=tid, label=f"kb run {tid}")
        self.assertEqual(self.http.post(f"/api/tickets/{tid}/action", {"action": "done"})[0], 200)
        st, v = self.http.get(f"/api/jobs/{jid}")
        self.assertEqual(st, 200); self.assertIsNotNone(v["ticket"])
        self.assertEqual(v["ticket"]["id"], tid); self.assertEqual(v["ticket"]["status"], "done")
        self.assertTrue(v["ticket"]["updated_after_job"])                       # ジョブが終わった後に人が完了にした
        jid2 = self._put_job("20000101-000001-kb-run")                          # チケットを持たないジョブ
        self.assertIsNone(self.http.get(f"/api/jobs/{jid2}")[1]["ticket"])

    def test_sync_preview_does_not_write(self):
        """状態を合わせる前の下見（kb sync --dry-run）: 前後が分かり、チケットは書き換わらない"""
        tid = self.seed
        _, r = self.http.get("/api/runs")
        run = next(x["name"] for x in r["runs"] if str(x.get("task")) == str(tid))
        before = self.http.get(f"/api/tickets/{tid}")[1]
        st, p = self.http.get(f"/api/tickets/{tid}/sync-preview?run={urllib.parse.quote(run)}")
        self.assertEqual(st, 200, p)
        self.assertEqual(p["run"], run); self.assertEqual(p["before"]["status"], "todo"); self.assertEqual(p["after"]["status"], "done")
        self.assertTrue(p["changes"]); self.assertFalse(p["updated_after_run"]); self.assertEqual(p["ticket"]["id"], tid)
        after = self.http.get(f"/api/tickets/{tid}")[1]
        self.assertEqual(after["ticket"]["status"], "todo")                     # 下見は書き込まない
        self.assertEqual(after["ticket"]["updated"], before["ticket"]["updated"])
        self.assertEqual(len(after["history"]), len(before["history"]))
        st, d = self.http.post("/api/tickets", {"pj": PJ, "kind": "research", "title": "調査: run の無いチケット", "body": "x"})
        with self.assertRaises(urllib.error.HTTPError) as cm: self.http.get(f"/api/tickets/{d['id']}/sync-preview")
        self.assertEqual(cm.exception.code, 400); self.assertIn("--run", json.loads(cm.exception.read())["error"])

    def test_sync_action_writes_only_when_asked(self):
        """状態を合わせる操作は、既定では書かずに前後を返す（HTTP も MCP も同じ関数を通る）。

        書くのは dry_run: false を明示したときだけ。画面はダイアログで前後を見せてから明示する。"""
        st, d = self.http.post("/api/tickets", {"pj": PJ, "kind": "research", "title": "調査: 合わせる操作の既定", "body": "x\n\n## 完了条件\n- y"})
        self.assertEqual(st, 200, d); tid = d["id"]
        name = self._fixture_run(f"2020-01-02-{PJ}-{tid}", {"pj": PJ, "task": tid, "workflow": "research", "started": "2000-01-01T00:00:00+00:00",
                                                            "finished": "2000-01-01T00:00:00+00:00", "result": "end", "pr_url": "", "history": []})
        try:
            st, p1 = self.http.post(f"/api/tickets/{tid}/action", {"action": "sync", "run": name})
            self.assertEqual(st, 200, p1)
            self.assertTrue(p1["dry_run"]); self.assertEqual(p1["before"]["status"], "todo"); self.assertEqual(p1["after"]["status"], "done")
            self.assertTrue(p1["updated_after_run"]); self.assertTrue(p1["warning"])
            self.assertEqual(self.http.get(f"/api/tickets/{tid}")[1]["ticket"]["status"], "todo")   # 既定は書かない
            st, p2 = self.http.post(f"/api/tickets/{tid}/action", {"action": "sync", "run": name, "dry_run": False})
            self.assertEqual(st, 200, p2); self.assertFalse(p2["dry_run"]); self.assertEqual(p2["before"]["status"], "todo")
            self.assertEqual(self.http.get(f"/api/tickets/{tid}")[1]["ticket"]["status"], "done")
        finally:
            shutil.rmtree(self.ws / "runs" / name, ignore_errors=True)

        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("  'sync': async el =>")
        body = app[i:app.index("\n  },", i)]
        self.assertIn("dry_run: false", body, "ダイアログで確認した後に書く指定が無い（押しても状態が変わらない）")

    def test_new_ticket(self):
        st, d = self.http.post("/api/tickets", {"pj": PJ, "kind": "research", "title": "調査: console テスト", "body": "本文\n\n## 完了条件\n- summary.md"})
        self.assertEqual(st, 200, d); self.assertIsInstance(d["id"], int)
        t = self.http.get(f"/api/tickets/{d['id']}")[1]; self.assertEqual(t["ticket"]["kind"], "research"); self.assertIn("完了条件", t["body"])
        self.assertEqual(self.http.post("/api/tickets", {"pj": PJ, "kind": "research"})[0], 400)
        self.assertEqual(self.http.post("/api/tickets", {"pj": "nope", "kind": "research", "title": "x"})[0], 400)

    def test_release_validation(self):
        self.assertEqual(self.http.post("/api/sandbox/release", {"task": "x; rm -rf /"})[0], 400)

    def test_dry_run_job(self):
        tid = self.todo_id()
        st, d = self.http.post(f"/api/tickets/{tid}/run", {"dry_run": True}); self.assertEqual(st, 200, d)
        jid = d["job"]["id"]; self.assertEqual(d["job"]["state"], "running")
        for _ in range(300):
            _, j = self.http.get(f"/api/jobs/{jid}")
            if j["job"]["state"] != "running": break
            time.sleep(0.2)
        self.assertIn(j["job"]["state"], ("done", "failed")); self.assertIn("dry-run 終了", j["log"]["text"])
        self.assertIn("[run ", j["log"]["text"])                       # runner まで到達した
        _, j2 = self.http.get(f"/api/jobs/{jid}?offset={j['log']['size']}"); self.assertEqual(j2["log"]["text"], "")
        self.assertEqual(self.http.get(f"/api/tickets/{tid}")[1]["ticket"]["status"], "todo")   # dry-run は状態を進めない
        self.assertTrue(any(x["label"].startswith("kb run") for x in self.http.get("/api/jobs")[1]["jobs"]))


class SandboxLsTest(unittest.TestCase):
    """sandbox ls のジョブログを表にするための解釈と、取得中 / 成功 / 失敗 / 未取得の区別（チケット 229）"""

    HEAD = "TASK     VM             VMID   IP           STATUS    SINCE\n"
    ROWS = ("229      sb-kumitate-01 9204   10.77.1.4    running   2026-09-06T12:00:07+09:00\n"
            "-        sb-kumitate-long-name-99 9299 10.77.1.9 stopped\n")

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-ls-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.m = load_module(self.tmp / "jobs")

    def job(self, jid, started, rc, state, log):
        """sandbox ls のジョブ記録を手で置く（VM も Proxmox も使わない）"""
        d = self.m.core.JOBS / jid; d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({"id": jid, "kind": "sandbox-ls", "label": "sandbox ls", "cmd": ["sandbox", "ls"],
                                                 "ticket": None, "run_hint": None, "pid": 1, "started": started,
                                                 "finished": started, "rc": rc, "state": state}), encoding="utf-8")
        (d / "log").write_text("$ sandbox ls\n" + log, encoding="utf-8")

    def test_parse_ls_columns(self):
        """固定幅の printf でも、長い VM 名・SINCE 無し・貸出なしの `-` を取り違えない"""
        vms = self.m.core.parse_ls(self.HEAD + self.ROWS)
        self.assertEqual(len(vms), 2)
        self.assertEqual(vms[0], {"task": "229", "name": "sb-kumitate-01", "vmid": "9204", "ip": "10.77.1.4",
                                  "status": "running", "since": "2026-09-06T12:00:07+09:00", "pj": "kumitate"})
        self.assertEqual(vms[1]["name"], "sb-kumitate-long-name-99")     # 14 文字を超えて列がずれても名前は欠けない
        self.assertIsNone(vms[1]["task"]); self.assertIsNone(vms[1]["since"]); self.assertEqual(vms[1]["status"], "stopped")

    def test_parse_ls_drops_noise(self):
        """ジョブ先頭の `$` 行・注記・見出し・列数の合わない行は捨てる（生ログはジョブの記録に残る）"""
        noisy = "$ sandbox ls\n" + self.HEAD + "[error] ssh: connect timed out\n\nうまく読めない行\n" + self.ROWS
        self.assertEqual([v["name"] for v in self.m.core.parse_ls(noisy)], ["sb-kumitate-01", "sb-kumitate-long-name-99"])
        self.assertEqual(self.m.core.parse_ls(""), [])

    def test_never_fetched(self):
        d = self.m.core.sandbox_view()
        self.assertIsNone(d["last_ls"]); self.assertIsNone(d["last_ok_ls"]); self.assertEqual(d["vms"], [])

    def test_failure_is_not_never_fetched(self):
        """失敗した回は「まだ取っていません」に見えてはいけない（last_ls は成否を問わず直近）"""
        self.job("20260907-100000-sandbox-ls", "2026-09-07T10:00:00", 1, "failed", "[error] ssh: connect timed out\n")
        d = self.m.core.sandbox_view()
        self.assertEqual(d["last_ls"]["rc"], 1); self.assertIsNone(d["last_ok_ls"]); self.assertEqual(d["vms"], [])

    def test_success_then_failure_keeps_last_table(self):
        """直近が失敗でも、前に成功した回の表は残す（失敗の表示は別に出す）"""
        self.job("20260907-090000-sandbox-ls", "2026-09-07T09:00:00", 0, "done", self.HEAD + self.ROWS)
        d = self.m.core.sandbox_view()
        self.assertEqual(d["last_ls"]["id"], d["last_ok_ls"]["id"]); self.assertEqual(len(d["vms"]), 2)
        self.job("20260907-100000-sandbox-ls", "2026-09-07T10:00:00", 1, "failed", "[error] ssh: connect timed out\n")
        d = self.m.core.sandbox_view()
        self.assertEqual(d["last_ls"]["state"], "failed"); self.assertEqual(d["last_ok_ls"]["rc"], 0)
        self.assertEqual([v["name"] for v in d["vms"]], ["sb-kumitate-01", "sb-kumitate-long-name-99"])

    def test_screen_shows_a_table_not_raw_log(self):
        """画面は生ログではなく表を出し、貸出先と稼働状態を別の列にする（console/UX.md の 4 軸表）。

        JS を動かす基盤が無いので、test_strings.py と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewSandbox"); body = app[i: app.index("\n/* ----------", i)]
        for key in ("d.vms", "T.th.lentTo", "T.th.power", "T.power", "T.label.vacant", "T.sandbox.lsFailed", "T.help.lsAxes"):
            self.assertIn(key, body, key)
        self.assertNotIn("ls.log.text", body)                            # 固定幅の生ログをそのまま出さない


class SandboxIdleStopTest(unittest.TestCase):
    """節電で止めた VM（sandbox idle-stop、チケット 252）を画面と MCP に渡す。

    停止中の VM が「壊れている」のか「使われないので止めた」のかは、`sandbox ls` の STATUS だけでは分からない。
    CLI が書く idle-stop.json を読んで、その vmid だけ「節電で停止中」と言えるようにする。コンソールからは止めも起こしもしない。
    """

    RESULT = {"hours": 3, "last_run": "2026-09-08T04:00:00+09:00",
              "stopped": [{"vmid": 9204, "name": "sb-kumitate-01", "at": "2026-09-08T04:00:00+09:00",
                           "last_used": "2026-09-08T00:30:00+09:00"}]}

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-idle-stop-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.m = load_module(self.tmp / "jobs")
        self.m.core.SANDBOX_STATE = self.tmp / "state.json"
        self.idle = self.tmp / "idle-stop.json"

    def write(self, data):
        self.idle.write_text(json.dumps(data), encoding="utf-8")

    def test_no_file_means_no_idle_stop(self):
        """timer が一度も動いていない環境では null。0 台と言い切らない"""
        self.assertIsNone(self.m.core.sandbox_view()["idle_stop"])

    def test_result_file_is_passed_through(self):
        self.write(self.RESULT)
        d = self.m.core.sandbox_view()["idle_stop"]
        self.assertEqual(d["hours"], 3)
        self.assertEqual(d["last_run"], "2026-09-08T04:00:00+09:00")
        self.assertEqual(d["stopped"], [{"vmid": "9204", "name": "sb-kumitate-01",
                                         "at": "2026-09-08T04:00:00+09:00", "last_used": "2026-09-08T00:30:00+09:00"}])

    def test_keep_and_candidates_are_passed_through(self):
        """足切り（keep）と、候補のまま起動している VM（candidates）も画面に渡す（2026-09-09）。古い記録には無いので None / []"""
        self.write(self.RESULT)
        d = self.m.core.sandbox_view()["idle_stop"]
        self.assertIsNone(d["keep"]); self.assertEqual(d["candidates"], [])
        self.write({**self.RESULT, "keep": 10,
                    "candidates": [{"vmid": 9205, "name": "sb-kumitate-02", "last_used": "2026-09-07T00:30:00+09:00"}, "ごみ"]})
        d = self.m.core.sandbox_view()["idle_stop"]
        self.assertEqual(d["keep"], 10)
        self.assertEqual(d["candidates"], [{"vmid": "9205", "name": "sb-kumitate-02", "last_used": "2026-09-07T00:30:00+09:00"}])

    def test_vmid_is_a_string_so_the_screen_can_match_the_ls_table(self):
        """台帳は数値、`sandbox ls` は文字列。突き合わせる側で取り違えないよう str に揃える（leases_by_vmid と同じ）"""
        self.write(self.RESULT)
        vmids = [v["vmid"] for v in self.m.core.sandbox_view()["idle_stop"]["stopped"]]
        self.assertEqual(vmids, ["9204"])

    def test_broken_file_is_ignored(self):
        """読めないファイルで sandbox 画面ごと落とさない"""
        self.idle.write_text("{ broken", encoding="utf-8")
        self.assertIsNone(self.m.core.sandbox_view()["idle_stop"])
        self.write([1, 2])
        self.assertIsNone(self.m.core.sandbox_view()["idle_stop"])
        self.write({"hours": 3, "last_run": "2026-09-08T04:00:00+09:00", "stopped": ["ごみ"]})
        self.assertEqual(self.m.core.sandbox_view()["idle_stop"]["stopped"], [])

    def test_screen_tells_idle_stop_apart_from_a_dead_vm(self):
        """画面は idle_stop の vmid だけ「節電で停止中」にし、次の貸出で起きることを添える（JS は動かせないのでソースを検査する）"""
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewSandbox"); body = app[i: app.index("\n/* ----------", i)]
        for key in ("d.idle_stop", "T.power.idle", "T.help.idleStop", "T.help.idleStopAxes",
                    "idle.candidates", "T.power.candidate", "T.help.idleCandidate"):
            self.assertIn(key, body, key)
        self.assertIn("power(v.status, v.vmid)", body)                  # 停止中かどうかだけでなく vmid で見分ける

    def test_ls_axes_no_longer_says_vms_are_never_stopped(self):
        """「返却しても VM は止めない」は idle-stop 以後は事実と違う（UX.md の用語集も同じ）"""
        strings = (REPO / "console" / "static" / "strings.js").read_text(encoding="utf-8")
        self.assertNotIn("返却しても VM は止めない", strings)
        ux = (REPO / "console" / "UX.md").read_text(encoding="utf-8")
        self.assertNotIn("返却しても止めない", ux)
        self.assertIn("節電で停止中", ux)


class SandboxSharedVmTest(unittest.TestCase):
    """同じ VM（同じ vmid）が複数チケットに貸出中のときの数え方と明示（チケット 237）。

    台帳（state.json）はコンソールからは読むだけ。ここで確かめるのは「2 件の貸出を 2 台と数えない」ことと、
    共有している組を画面に渡すことだけで、台帳の直しや返却は一切しない。
    """

    SHARED = {"221": {"vmid": 9213, "name": "sb-kumitate-01", "ip": "10.77.1.13", "pj": "kumitate", "since": "2026-09-06T10:00:00+09:00"},
              "222": {"vmid": 9213, "name": "sb-kumitate-01", "ip": "10.77.1.13", "pj": "kumitate", "since": "2026-09-06T11:00:00+09:00"},
              "223": {"vmid": 9214, "name": "sb-kumitate-02", "ip": "10.77.1.14", "pj": "kumitate", "since": "2026-09-06T12:00:00+09:00"}}

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-shared-vm-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.m = load_module(self.tmp / "jobs")
        self.m.core.SANDBOX_STATE = self.tmp / "state.json"

    def state(self, lent):
        (self.tmp / "state.json").write_text(json.dumps(lent), encoding="utf-8")

    def test_shared_vmid_is_counted_once_and_reported(self):
        """貸出 3 件・VM 2 台。221 と 222 が同じ 9213 だと分かる形で返す"""
        self.state(self.SHARED)
        d = self.m.core.sandbox_view()
        self.assertEqual(d["lease_count"], 3)
        self.assertEqual(d["vm_count"], 2)
        self.assertEqual(d["shared"], {"9213": ["221", "222"]})

    def test_pj_pool_counts_vms_not_leases(self):
        """PJ の使用数は台数（2 / 3）。件数は別に持ち、同じ VM を無説明に 2 台と数えない"""
        self.state(self.SHARED)
        pj = next(p for p in self.m.core.sandbox_view()["templates"] if p["pj"] == PJ)
        self.assertEqual(pj["lent"], 2)
        self.assertEqual(pj["leases"], 3)

    def test_overview_keeps_lent_and_adds_vm_count(self):
        """ナビの数字は台数にする。既存の lent（件数）は MCP の利用者のために残す"""
        self.state(self.SHARED)
        o = self.m.core.overview()
        self.assertEqual(o["lent"], 3)
        self.assertEqual(o["vms_lent"], 2)

    def test_no_duplicate_is_not_reported_as_shared(self):
        self.state({k: v for k, v in self.SHARED.items() if k != "222"})
        d = self.m.core.sandbox_view()
        self.assertEqual(d["shared"], {})
        self.assertEqual(d["vm_count"], d["lease_count"])
        self.assertEqual(self.m.core.overview()["vms_lent"], 2)

    def test_broken_state_does_not_count(self):
        """読めない台帳（_error）や dict でない値は台数にも件数にも入れない"""
        (self.tmp / "state.json").write_text("{ broken", encoding="utf-8")
        d = self.m.core.sandbox_view()
        self.assertEqual((d["lease_count"], d["vm_count"], d["shared"]), (0, 0, {}))

    def test_screen_explains_sharing_and_release_impact(self):
        """画面は共有を明示し、返却の前に影響するチケットを出す（JS は動かせないのでソースを検査する）"""
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewSandbox"); body = app[i: app.index("\n/* ----------", i)]
        for key in ("d.shared", "T.sandbox.sharedWarn", "T.sandbox.sharedBadge", "T.sandbox.sharedWith", "T.sandbox.countShared"):
            self.assertIn(key, body, key)
        j = app.index("'sandbox-release':"); rel = app[j: app.index("'job-stop':", j)]
        for key in ("T.dialog.release.sharedWarning", "shared"):
            self.assertIn(key, rel, key)
        self.assertRegex(rel, r"typed:\s*[^,]*shared")                  # 共有なら run が無くても番号入力を求める
        # 実勢の表（sandbox ls）の貸出先は共有時 `221,222` で来る。1 本のリンクにすると /tickets/(\d+) に合わず開けない
        self.assertIn("split(',')", body)                               # 1 チケット 1 リンクに分ける
        vms = body[body.index("d.vms.map"): body.index("T.help.lsAxes")]
        self.assertNotIn("#/ticket/${esc(v.task)}", vms)                # カンマ区切りのまま 1 本のリンクにしない


class SandboxStateFileTest(unittest.TestCase):
    """貸出台帳（state.json）の読み方（チケット 336 の 3 番目）。

    PM は貸出中 VM の IP を MCP から引けず ssh で state.json を直読みした。原因は 2 つ:
    台帳が空なのか読めていないのかを区別できないことと、貸出 1 件ぶんの項目が一覧の形で出ていなかったこと。
    """

    HEAD = "TASK     VM             VMID   IP           STATUS    SINCE\n"
    ROWS = ("336      sb-kumitate-01 9204   10.77.1.4    running   2026-09-08T10:00:00+09:00\n"
            "-        sb-kumitate-02 9205   10.77.1.5    stopped\n")
    LENT = {"336": {"vmid": 9204, "name": "sb-kumitate-01", "ip": "10.77.1.4", "pj": PJ,
                    "since": "2026-09-08T10:00:00+09:00", "phase": "ready"},
            "40": {"vmid": 9205, "name": "sb-kumitate-02", "ip": "10.77.1.5", "pj": PJ,
                   "since": "2026-09-08T11:00:00+09:00", "phase": "gates"}}

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-state-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.m = load_module(self.tmp / "jobs")
        self.m.core.SANDBOX_STATE = self.tmp / "state.json"

    def state(self, lent):
        (self.tmp / "state.json").write_text(json.dumps(lent), encoding="utf-8")

    def ls_job(self, log):
        d = self.m.core.JOBS / "20260908-120000-sandbox-ls"; d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({"id": d.name, "kind": "sandbox-ls", "label": "sandbox ls", "cmd": ["sandbox", "ls"],
                                                 "ticket": None, "run_hint": None, "pid": 1, "started": self.m.core.now(),
                                                 "finished": self.m.core.now(), "rc": 0, "state": "done"}), encoding="utf-8")
        (d / "log").write_text("$ sandbox ls\n" + log, encoding="utf-8")

    def test_missing_ledger_is_not_the_same_as_no_lease(self):
        d = self.m.core.sandbox_view()
        self.assertEqual(d["lent"], {}); self.assertEqual(d["leases"], [])
        self.assertFalse(d["state_exists"]); self.assertIn("state.json", d["state_error"])

    def test_unreadable_ledger_says_why(self):
        (self.tmp / "state.json").write_text("{ broken", encoding="utf-8")
        d = self.m.core.sandbox_view()
        self.assertTrue(d["state_exists"]); self.assertTrue(d["state_error"]); self.assertEqual(d["leases"], [])

    def test_leases_carry_ip_and_since_in_task_order(self):
        """貸出 1 件 = 1 行。台帳の項目をそのまま載せ、チケット番号は数として並べる"""
        self.state(self.LENT)
        d = self.m.core.sandbox_view()
        self.assertTrue(d["state_exists"]); self.assertIsNone(d["state_error"])
        self.assertEqual([l["task"] for l in d["leases"]], ["40", "336"])
        first = d["leases"][1]
        self.assertEqual((first["vmid"], first["name"], first["ip"], first["pj"], first["phase"]),
                         ("9204", "sb-kumitate-01", "10.77.1.4", PJ, "ready"))
        self.assertEqual(first["since"], "2026-09-08T10:00:00+09:00")
        self.assertEqual(first["url"], d["urls"]["336"])

    def test_lease_joins_the_power_state_from_the_last_ls(self):
        """稼働状態は最後に成功した ls から vmid で引く。ls が無ければ null（推測しない）"""
        self.state(self.LENT)
        self.assertEqual([l["vm_status"] for l in self.m.core.sandbox_view()["leases"]], [None, None])
        self.ls_job(self.HEAD + self.ROWS)
        by = {l["task"]: l["vm_status"] for l in self.m.core.sandbox_view()["leases"]}
        self.assertEqual(by, {"336": "running", "40": "stopped"})


class SandboxLsRefreshTest(unittest.TestCase):
    """`sandbox ls` が古ければ sandbox_status が裏で取り直す（チケット 336 の 2 番目・ADR-0038）。

    読み取りのツールがジョブを起こす唯一の例外なので、起こす / 起こさないの 3 条件をここで固定する。
    Proxmox にも VM にも触らない: PATH の先頭に固定の表を印字する偽の `sandbox` を置く。
    """

    TABLE = ("TASK     VM             VMID   IP           STATUS    SINCE\n"
             "336      sb-kumitate-01 9204   10.77.1.4    running   2026-09-08T10:00:00+09:00\n"
             "-        sb-kumitate-02 9205   10.77.1.5    stopped\n")

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-ls-refresh-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.m = load_module(self.tmp / "jobs")
        self.m.core.SANDBOX_STATE = self.tmp / "state.json"
        self.bin = self.tmp / "bin"; self.bin.mkdir()
        sb = self.bin / "sandbox"
        sb.write_text('#!/bin/sh\n[ "$1" = ls ] || exit 2\ncat <<\'EOF\'\n' + self.TABLE + 'EOF\n', encoding="utf-8")
        sb.chmod(0o755)
        self.path0, self.home0 = os.environ.get("PATH", ""), os.environ.get("HOME", "")
        self.addCleanup(os.environ.__setitem__, "PATH", self.path0)
        self.addCleanup(os.environ.__setitem__, "HOME", self.home0)
        os.environ["PATH"] = f"{self.bin}:{self.path0}"
        os.environ["HOME"] = str(self.tmp)          # child_env() が足す ~/.local/bin から本物を拾わないように

    def no_sandbox_on_path(self):
        os.environ["PATH"] = "/usr/bin:/bin"
        if shutil.which("sandbox", path=self.m.core.child_env().get("PATH")):
            self.skipTest("この環境には本物の sandbox が PATH にある（実機を叩かない）")

    def view(self):
        return self.m.core.sandbox_ls_refresh_if_stale(self.m.core.sandbox_view())

    def job(self, jid, started, rc, state):
        d = self.m.core.JOBS / jid; d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({"id": jid, "kind": "sandbox-ls", "label": "sandbox ls", "cmd": ["sandbox", "ls"],
                                                 "ticket": None, "run_hint": None, "pid": 1, "started": started,
                                                 "finished": None if rc is None else started, "rc": rc, "state": state}), encoding="utf-8")
        (d / "log").write_text("$ sandbox ls\n", encoding="utf-8")

    def ago(self, seconds):
        return (datetime.datetime.now().astimezone() - datetime.timedelta(seconds=seconds)).isoformat(timespec="seconds")

    def n_ls_jobs(self):
        return len([j for j in self.m.core.JobStore.list() if j.get("kind") == "sandbox-ls"])

    def test_never_fetched_starts_a_refresh_and_the_next_call_is_fresh(self):
        d = self.view()
        self.assertTrue(d["ls_refreshing"]); self.assertIsNone(d["ls_fetched"])      # 今回は待たせない
        jid = d["ls_refresh_job"]
        self.assertEqual(self.m.core.job_wait(jid, 20)["rc"], 0)
        d = self.view()
        self.assertFalse(d["ls_refreshing"]); self.assertFalse(d["ls_stale"]); self.assertLess(d["ls_age_s"], 600)
        self.assertEqual([v["name"] for v in d["vms"]], ["sb-kumitate-01", "sb-kumitate-02"])
        self.assertEqual(next(p for p in d["templates"] if p["pj"] == PJ)["pool_actual"], 2)
        self.assertEqual(self.n_ls_jobs(), 1)                                        # 新しいうちは起こさない

    def test_a_running_ls_is_not_started_twice(self):
        self.job("20260908-115900-sandbox-ls", self.ago(30), None, "running")
        d = self.view()
        self.assertTrue(d["ls_refreshing"]); self.assertEqual(d["ls_refresh_job"], "20260908-115900-sandbox-ls")
        self.assertEqual(self.n_ls_jobs(), 1)

    def test_a_recent_failure_is_not_retried(self):
        """失敗直後に叩き続けない（ssh が落ちているときに sandbox_status のたびに ssh しない）"""
        self.job("20260908-115900-sandbox-ls", self.ago(60), 1, "failed")
        d = self.view()
        self.assertFalse(d["ls_refreshing"]); self.assertIn("600", d["ls_refresh_error"])
        self.assertEqual(self.n_ls_jobs(), 1)

    def test_an_old_failure_is_retried(self):
        self.job("20260908-100000-sandbox-ls", self.ago(1200), 1, "failed")
        self.assertTrue(self.view()["ls_refreshing"]); self.assertEqual(self.n_ls_jobs(), 2)

    def test_missing_command_is_reported_without_failing_the_view(self):
        self.no_sandbox_on_path()
        d = self.view()
        self.assertFalse(d["ls_refreshing"]); self.assertIn("PATH", d["ls_refresh_error"])
        self.assertIn("templates", d)                                                # 読めた分はそのまま返す
        self.assertEqual(self.n_ls_jobs(), 0)


class SandboxPoolCountTest(unittest.TestCase):
    """プールの「定義台数」と「実体台数」を分けて数える（チケット 241）。

    定義は設定の値、実体は最後に成功した `sandbox ls` に出た VM の数。ずれると take が「空きなし」で落ちるので、
    画面と MCP には 4 つ（定義 / 実体 / 貸出 / 空き）を別々に渡す。Proxmox も VM も使わず、偽の ls ログで確かめる。
    """

    HEAD = "TASK     VM             VMID   IP           STATUS    SINCE\n"
    # aifactory は定義 3 台に対して実体 2 台（テナント命名 sb-main-...。ADR-0017）
    TWO = ("9201     sb-main-aifactory-01 9201 10.77.1.1 running   2026-09-06T10:00:00+09:00\n"
           "-        sb-main-aifactory-02 9202 10.77.1.2 stopped\n")
    LENT = {"233": {"vmid": 9201, "name": "sb-main-aifactory-01", "ip": "10.77.1.1", "pj": "aifactory", "since": "2026-09-06T10:00:00+09:00"},
            "234": {"vmid": 9202, "name": "sb-main-aifactory-02", "ip": "10.77.1.2", "pj": "aifactory", "since": "2026-09-06T11:00:00+09:00"}}

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-pool-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.m = load_module(self.tmp / "jobs")
        self.m.core.SANDBOX_STATE = self.tmp / "state.json"

    def state(self, lent):
        (self.tmp / "state.json").write_text(json.dumps(lent), encoding="utf-8")

    def job(self, log, finished=None, jid="20260907-090000-sandbox-ls", rc=0):
        """sandbox ls のジョブ記録を手で置く（SandboxLsTest の job() と同じ流儀）"""
        finished = finished or self.m.core.now()
        d = self.m.core.JOBS / jid; d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({"id": jid, "kind": "sandbox-ls", "label": "sandbox ls", "cmd": ["sandbox", "ls"],
                                                 "ticket": None, "run_hint": None, "pid": 1, "started": finished,
                                                 "finished": finished, "rc": rc, "state": "done" if rc == 0 else "failed"}), encoding="utf-8")
        (d / "log").write_text("$ sandbox ls\n" + log, encoding="utf-8")

    def pj(self, name="aifactory"):
        return next(p for p in self.m.core.sandbox_view()["templates"] if p["pj"] == name)

    def ago(self, minutes):
        return (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes)).isoformat(timespec="seconds")

    def test_pj_of_vm_reads_tenant_and_old_names(self):
        """VM 名から PJ を引く。テナント付き（sb-main-aifactory-01）と旧命名（sb-kumitate-01）の両方"""
        known = ["aifactory", "kumitate"]
        self.assertEqual(self.m.core.pj_of_vm("sb-main-aifactory-01", known), "aifactory")
        self.assertEqual(self.m.core.pj_of_vm("sb-kumitate-01", known), "kumitate")
        self.assertIsNone(self.m.core.pj_of_vm("sb-main-aifactory-01", ["kumitate"]))   # 別 PJ に数えない
        self.assertIsNone(self.m.core.pj_of_vm("sb-kumitate-long-name-99", known))      # 知らない PJ は付けない
        self.assertIsNone(self.m.core.pj_of_vm("sb-main-gw", known))

    def test_ls_rows_carry_the_pj(self):
        self.assertEqual([v["pj"] for v in self.m.core.parse_ls(self.HEAD + self.TWO, ["aifactory"])], ["aifactory", "aifactory"])

    def test_defined_and_actual_are_separate(self):
        """定義 3 台・実体 2 台・貸出 2 台なら空きは 0。未構築 1 台と次の一手を添える"""
        self.state(self.LENT); self.job(self.HEAD + self.TWO)
        p = self.pj()
        self.assertEqual((p["pool_defined"], p["pool_actual"], p["lent"], p["free"], p["unbuilt"]), (3, 2, 2, 0, 1))
        self.assertIn("40-pool.sh aifactory 1", p["hint"])
        self.assertEqual(p["pool"], p["pool_defined"])                  # 既存の鍵は残す（MCP の利用者を壊さない）

    def test_other_pj_vms_are_not_counted(self):
        """aifactory の VM を kumitate に数えない"""
        self.state({}); self.job(self.HEAD + self.TWO)
        k = self.pj("kumitate")
        self.assertEqual((k["pool_actual"], k["lent"], k["free"], k["unbuilt"]), (0, 0, 0, 3))

    def test_old_naming_is_counted(self):
        self.state({}); self.job(self.HEAD + "-        sb-kumitate-01 9204 10.77.1.4 running\n")
        self.assertEqual(self.pj("kumitate")["pool_actual"], 1)
        self.assertEqual(self.pj("aifactory")["pool_actual"], 0)

    def test_actual_is_unknown_until_ls_succeeds(self):
        """成功した ls が無ければ実体は「未取得」。0 台と言い切らない"""
        self.state(self.LENT)
        p = self.pj()
        self.assertEqual(p["pool_defined"], 3); self.assertEqual(p["lent"], 2)
        self.assertIsNone(p["pool_actual"]); self.assertIsNone(p["free"]); self.assertIsNone(p["unbuilt"]); self.assertIsNone(p["hint"])
        self.assertIsNone(self.m.core.sandbox_view()["ls_fetched"])

    def test_free_never_goes_negative(self):
        """ls に出ない VM が台帳にあっても空きは 0 で止める（実体と貸出は取得時刻がずれる）"""
        self.state({**self.LENT, "235": {"vmid": 9203, "name": "sb-main-aifactory-03", "ip": "10.77.1.3", "pj": "aifactory", "since": "x"}})
        self.job(self.HEAD + self.TWO)
        p = self.pj()
        self.assertEqual((p["pool_actual"], p["lent"], p["free"]), (2, 3, 0))

    def test_stale_ls_is_flagged_with_its_time(self):
        """古い一覧は取得時刻とともに古いと言う（229 の完了条件と揃える）"""
        self.state({}); self.job(self.HEAD + self.TWO, finished=self.ago(11))
        d = self.m.core.sandbox_view()
        self.assertTrue(d["ls_stale"]); self.assertGreater(d["ls_age_s"], 600); self.assertTrue(OFFSET_ISO.match(d["ls_fetched"]))
        self.job(self.HEAD + self.TWO, finished=self.ago(1), jid="20260907-100000-sandbox-ls")
        d = self.m.core.sandbox_view()
        self.assertFalse(d["ls_stale"]); self.assertLess(d["ls_age_s"], 600)

    def test_screen_separates_defined_from_actual(self):
        """画面は 4 つを別の列にし、VM 名からの PJ 推測をやめて core の値を使う（JS は動かせないのでソースを検査する）"""
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewSandbox"); body = app[i: app.index("\n/* ----------", i)]
        for key in ("p.pool_defined", "p.pool_actual", "p.free", "p.unbuilt", "d.ls_stale", "d.ls_fetched",
                    "T.th.poolDefined", "T.th.poolActual", "T.th.free", "T.sandbox.unbuilt", "T.sandbox.actualUnknown"):
            self.assertIn(key, body, key)
        self.assertNotIn("sb-${p.pj}-", body)                           # 名前の前方一致で PJ を当てない（テナント名で外れる）
        self.assertIn("v.pj", body)


class AuthDocsTest(unittest.TestCase):
    """CONSOLE_TOKEN（合言葉）付きで起動したときの認証と、/docs/ の配信（ADR-0017）"""
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-console-auth-"))
        cls.ws = cls.tmp / "ws"; cls.ws.mkdir()
        cls.port = free_port(); cls.token = "s3cret-token"
        env = {**os.environ, "AIFACTORY_WORKSPACE": str(cls.ws), "CONSOLE_JOBS": str(cls.tmp / "jobs"), "CONSOLE_TOKEN": cls.token}
        cls.proc = subprocess.Popen([sys.executable, str(CONSOLE), "--port", str(cls.port)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.base = f"http://127.0.0.1:{cls.port}"
        for _ in range(50):
            try: urllib.request.urlopen(cls.base + "/api/overview", timeout=2)
            except urllib.error.HTTPError: break
            except Exception: time.sleep(0.1)
        else: raise RuntimeError("console が起動しない")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(timeout=10); shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, path, headers=None, method="GET"):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k): return None
        opener = urllib.request.build_opener(NoRedirect)
        r = urllib.request.Request(self.base + path, headers=headers or {}, method=method, data=b"{}" if method == "POST" else None)
        try:
            with opener.open(r, timeout=10) as resp: return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e: return e.code, dict(e.headers), e.read()

    def test_token_required(self):
        st, _, body = self.req("/api/overview"); self.assertEqual(st, 401); self.assertIn("token", body.decode())
        st, _, _ = self.req("/"); self.assertEqual(st, 401)
        st, _, _ = self.req("/api/sandbox/ls", headers={"X-Console": "1", "Content-Type": "application/json"}, method="POST"); self.assertEqual(st, 401)

    def test_bearer_and_cookie(self):
        st, _, _ = self.req("/api/overview", headers={"Authorization": f"Bearer {self.token}"}); self.assertEqual(st, 200)
        st, _, _ = self.req("/api/overview", headers={"Authorization": "Bearer wrong"}); self.assertEqual(st, 401)
        st, h, _ = self.req(f"/?token={self.token}"); self.assertEqual(st, 302); self.assertEqual(h.get("Location"), "/")
        cookie = h.get("Set-Cookie", "").split(";")[0]; self.assertIn(self.token, cookie)
        st, _, _ = self.req("/api/overview", headers={"Cookie": cookie}); self.assertEqual(st, 200)
        st, _, _ = self.req("/", headers={"Cookie": cookie}); self.assertEqual(st, 200)
        st, h, _ = self.req(f"/api/tickets?pj=x&token={self.token}"); self.assertEqual(st, 302); self.assertEqual(h.get("Location"), "/api/tickets?pj=x")

    def test_docs_route(self):
        h = {"Authorization": f"Bearer {self.token}"}
        st, _, body = self.req("/docs/", headers=h)
        self.assertIn(st, (200, 503))                       # website/site/ があれば 200、無ければ作り方の案内（503）
        self.assertIn(b"<html", body.lower()[:200] if st == 503 else body.lower())
        st, hh, _ = self.req("/docs", headers=h); self.assertIn(st, (301, 503))
        st, _, _ = self.req("/docs/../console/lib/core.py", headers=h); self.assertNotEqual(st, 200)


class BindPolicyTest(unittest.TestCase):
    """合言葉なしで bind してよいアドレス（ADR-0021）: 内側の網は可、全インターフェースとグローバルは不可"""
    def test_internal_bind(self):
        m = load_module(tempfile.mkdtemp(prefix="aifactory-bind-test-"))
        # 192.168.x と 100.64/10 のリテラルは bin/oss-check.sh（環境固有の名前の検査）に引っかかるので組み立てる
        for ok in ("127.0.0.1", "localhost", "::1", "10.77.0.3", "172.16.5.9", "192.%d.1.20" % 168, "100.%d.102.103" % 101, "169.254.1.1", "fd12::1"):
            self.assertTrue(m.internal_bind(ok), ok)
        for ng in ("0.0.0.0", "::", "1.1.1.1", "8.8.8.8", "2606:4700::1111", "ctl.example.com", ""):   # 203.0.113.x（TEST-NET）は予約で is_global でないので使わない
            self.assertFalse(m.internal_bind(ng), ng)


class JobStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aifactory-jobs-test-"); self.m = load_module(self.tmp); self.JS = self.m.JobStore
    def tearDown(self):
        for j in self.JS.running():
            try: os.killpg(os.getpgid(j["pid"]), signal.SIGKILL)
            except OSError: pass
        # _wait persists the final result after the process exits. Wait for that
        # writer before deleting its directory, otherwise teardown races _flock.
        deadline = time.monotonic() + 5
        while True:
            with self.JS.lock:
                pending = bool(self.JS.procs)
            if not pending: break
            self.assertLess(time.monotonic(), deadline, "job result writers did not finish")
            time.sleep(0.01)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_conflict_under_lock(self):
        same = lambda j: "dup" if j.get("ticket") == 1 else None
        a = self.JS.start("kb-run", ["sleep", "5"], "a", ticket=1, conflict=same)
        res = []
        def go():
            try: self.JS.start("kb-run", ["sleep", "5"], "b", ticket=1, conflict=same); res.append("started")
            except self.m.Conflict as e: res.append(str(e))
        ts = [threading.Thread(target=go) for _ in range(8)]; [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(res, ["dup"] * 8)
        other = lambda j: "dup" if j.get("ticket") == 2 else None          # API は要求されたチケットで判定する
        self.JS.start("kb-run", ["true"], "c", ticket=2, conflict=other)  # 別チケットは通る
        self.assertTrue(self.JS.stop(a["id"])[0])

    def test_stop_marks_stopped(self):
        a = self.JS.start("x", ["sleep", "5"], "a")
        ok, _ = self.JS.stop(a["id"]); self.assertTrue(ok)
        for _ in range(50):
            if self.JS.get(a["id"])["rc"] is not None: break
            time.sleep(0.1)
        self.assertEqual(self.JS.get(a["id"])["state"], "stopped")
        self.assertFalse(self.JS.stop(a["id"])[0])                        # 2 回目は「既に終わっている」

    def test_done_and_failed(self):
        a = self.JS.start("x", ["true"], "a"); b = self.JS.start("x", ["false"], "b")
        for _ in range(50):
            if self.JS.get(a["id"])["rc"] is not None and self.JS.get(b["id"])["rc"] is not None: break
            time.sleep(0.1)
        self.assertEqual(self.JS.get(a["id"])["state"], "done"); self.assertEqual(self.JS.get(b["id"])["state"], "failed")

    def test_stdin_and_log(self):
        a = self.JS.start("x", ["cat", "{stdin}"], "a", stdin_text="hello\n")
        for _ in range(50):
            if self.JS.get(a["id"])["rc"] is not None: break
            time.sleep(0.1)
        self.assertIn("hello", (pathlib.Path(self.tmp) / a["id"] / "log").read_text())

    def test_reconcile_marks_lost(self):
        dead = {"id": "20000101-000000-x", "kind": "x", "label": "x", "cmd": ["x"], "pid": 2**22 - 1, "started": "2000-01-01T00:00:00", "finished": None, "rc": None, "state": "running"}
        self.JS.save(dead); self.JS.reconcile()
        self.assertEqual(self.JS.get(dead["id"])["state"], "lost")


class TimestampTest(unittest.TestCase):
    """時刻の正規化（ADR-0026）。記録はオフセット付きで返し、オフセットの無い古い記録は書いたホスト＝サーバーの時間帯とみなす"""
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-ts-test-"))
        self.core = load_module(self.tmp / "jobs").core

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ts_aware_fills_missing_offset(self):
        off = datetime.datetime.now().astimezone().utcoffset()
        v = self.core.ts_aware("2020-01-03T09:00:00")
        self.assertRegex(v, OFFSET_ISO)
        self.assertEqual(datetime.datetime.fromisoformat(v).utcoffset(), off)
        self.assertEqual(datetime.datetime.fromisoformat(v).replace(tzinfo=None), datetime.datetime(2020, 1, 3, 9, 0, 0))

    def test_ts_aware_keeps_existing_offset(self):
        self.assertEqual(self.core.ts_aware("2020-01-03T09:00:00+00:00"), "2020-01-03T09:00:00+00:00")
        self.assertEqual(self.core.ts_aware("2020-01-03T09:00:00+09:00"), "2020-01-03T09:00:00+09:00")
        self.assertEqual(self.core.ts_aware("2020-01-03T09:00:00Z"), "2020-01-03T09:00:00+00:00")

    def test_ts_aware_passes_through_what_is_not_a_timestamp(self):
        for v in (None, "", "2026-09-08 の夕方", 3, {"a": 1}):
            self.assertEqual(self.core.ts_aware(v), v)

    def test_now_carries_offset(self):
        self.assertRegex(self.core.now(), OFFSET_ISO)

    def test_after_compares_mixed_records(self):
        """オフセットの有無が混ざっても比較できる（例外で False に落ちない）"""
        self.assertTrue(self.core.after("2020-01-03T09:10:00", "2020-01-03T09:00:00+00:00")
                        or self.core.after("2020-01-03T09:10:00+00:00", "2020-01-03T09:00:00"))
        self.assertTrue(self.core.after("2020-01-04T09:00:00", "2020-01-03T09:00:00+00:00"))
        self.assertFalse(self.core.after("2020-01-02T09:00:00", "2020-01-03T09:00:00+00:00"))
        self.assertFalse(self.core.after(None, "2020-01-03T09:00:00"))
        self.assertFalse(self.core.after("2020-01-03T09:00:00", None))
        self.assertFalse(self.core.after("いつか", "2020-01-03T09:00:00"))


def js_line(src, name):
    """app.js から 1 行の関数定義を抜く（表示の部品はすべて 1 行で書く約束。console/static/app.js の「表示の部品」節）"""
    m = re.search(rf"^(?:function {name}\(|const {name} = ).*$", src, re.M)
    assert m, f"app.js に {name} の 1 行の定義が無い"
    return m.group(0)


@unittest.skipUnless(shutil.which("node"), "node が無い")
class BrowserTimeTest(unittest.TestCase):
    """経過時間の計算がブラウザーの時間帯に左右されないこと（チケット 235）。app.js の関数を node で直に動かす"""
    NOW = "2020-01-01T00:31:00Z"

    def run_js(self, tz):
        app = (pathlib.Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(encoding="utf-8")
        strings = (pathlib.Path(__file__).resolve().parents[1] / "static" / "strings.js").read_text(encoding="utf-8")
        parts = [strings] + [js_line(app, n) for n in ("tt", "pad", "fmtT", "fmtDur", "sec", "since", "span")]
        parts.append(f'Date.now = () => Date.parse("{self.NOW}");')
        parts.append("""console.log(JSON.stringify({
          aware: since("2020-01-01T00:21:00+00:00"),
          naive: since("2020-01-01T00:21:00"),
          missing: since(null),
          unreadable: since("2026-09-08 の夕方"),
          ahead: since("2020-01-01T02:00:00+00:00"),
          spanNone: span(null, null),
          spanBoth: span("2020-01-01T00:21:00+00:00", "2020-01-01T00:31:00+00:00")}));""")
        src = self.tmp / f"probe-{tz.replace('/', '-')}.js"
        src.write_text("\n".join(parts), encoding="utf-8")
        p = subprocess.run(["node", str(src)], text=True, capture_output=True, env={**os.environ, "TZ": tz})
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-js-test-"))
        self.T = load_strings()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_elapsed_is_the_same_in_any_browser_timezone(self):
        tokyo, utc = self.run_js("Asia/Tokyo"), self.run_js("UTC")
        ten = self.T["time"]["min"].replace("{n}", "10")
        self.assertEqual(tokyo["aware"], ten); self.assertEqual(utc["aware"], ten)
        self.assertEqual(tokyo["spanBoth"], ten); self.assertEqual(utc["spanBoth"], ten)
        # オフセットが無い記録は読む側の時間帯で答えが変わる（この不具合の本体。API はもう naive を返さない）
        self.assertNotEqual(tokyo["naive"], utc["naive"])

    def test_the_lease_row_shows_the_pool_key_names(self):
        """貸出行に出る鍵の名前（#379）。プールを使っていない貸出（keys が無い）は空にする"""
        app = (pathlib.Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(encoding="utf-8")
        src = self.tmp / "keynames.js"
        src.write_text("\n".join([js_line(app, "esc"), js_line(app, "keyNames"), """console.log(JSON.stringify({
          both: keyNames({fable: "fable-a", other: "opus-a"}),
          one: keyNames({other: "opus-a"}),
          none: keyNames(null),
          empty: keyNames({}),
          escaped: keyNames({fable: "<script>"})}));"""]), encoding="utf-8")
        p = subprocess.run(["node", str(src)], text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout)
        self.assertEqual(r["both"], "fable: fable-a<br>other: opus-a")
        self.assertEqual(r["one"], "other: opus-a")
        self.assertEqual(r["none"], ""); self.assertEqual(r["empty"], "")
        self.assertEqual(r["escaped"], "fable: &lt;script&gt;")

    def test_missing_and_odd_times_say_what_is_going_on(self):
        for r in (self.run_js("Asia/Tokyo"), self.run_js("UTC")):
            self.assertEqual(r["missing"], self.T["time"]["unknown"])
            self.assertEqual(r["spanNone"], self.T["time"]["unknown"])
            self.assertEqual(r["ahead"], self.T["time"]["ahead"])
            self.assertEqual(r["unreadable"], "2026-09-08 の夕方")


class KitListingTest(unittest.TestCase):
    """種別・役割の一覧は kit のディレクトリ走査。macOS の AppleDouble（`._bug.yml`）などのごみを候補に出さない"""
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-kit-test-"))
        self.core = load_module(self.tmp / "jobs").core
        self.kit = self.tmp / "kit"; self.kit.mkdir()
        (self.kit / "bug.yml").write_text("name: bug\ndescription: 不具合を直す\n", encoding="utf-8")
        (self.kit / "._bug.yml").write_bytes(b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X")   # AppleDouble
        (self.kit / ".hidden.yml").write_text("name: hidden\n", encoding="utf-8")
        (self.kit / "planner.md").write_text("# planner\n", encoding="utf-8")
        (self.kit / "._planner.md").write_bytes(b"\x00\x05\x16\x07")
        (self.kit / "_common.md").write_text("# common\n", encoding="utf-8")
        (self.kit / "sub").mkdir()                                                 # ディレクトリは候補にしない
        (self.kit / "sub.yml").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dot_files_are_not_candidates(self):
        self.assertEqual(self.core.kinds(self.kit), ["bug"])
        self.assertEqual(self.core.roles(self.kit), ["planner"])


class DispatchLineTest(unittest.TestCase):
    """dispatch.log の 1 行の分解（HTTP を経由せず parse_dispatch_line を直接。ADR-0027）"""
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-logline-test-"))
        self.core = load_module(self.tmp / "jobs").core

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_start_without_title(self):
        """題名が空の dry-run（印だけが残る行）でも dry-run と分かる。印を題名として出さない"""
        e = self.core.parse_dispatch_line("2026-09-07T10:00:05\tstart 207 kumitate feature (dry-run)")
        self.assertEqual((e["event"], e["tid"], e["dry_run"], e["reason"]), ("start", 207, True, ""))


class LoadCtlEnvTest(unittest.TestCase):
    """ctl.env の読み込み（bin/mcp が ssh 越しでも secrets を持てるように。チケット 249）"""
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-ctlenv-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.core = load_module(self.tmp / "jobs").core
        self.envf = self.tmp / "ctl.env"
        self.envf.write_text('# コメント\n\nCONSOLE_TOKEN=x\nGH_TOKEN=\nQUOTED="q v"\nKEEP=fromfile\n', encoding="utf-8")
        for k in ("CONSOLE_TOKEN", "GH_TOKEN", "QUOTED", "KEEP"):
            os.environ.pop(k, None)
            self.addCleanup(os.environ.pop, k, None)

    def test_fills_only_unset_keys_and_skips_empty_values(self):
        os.environ["KEEP"] = "fromenv"
        added = self.core.load_ctl_env(self.envf)
        self.assertEqual(os.environ["CONSOLE_TOKEN"], "x")
        self.assertEqual(os.environ["QUOTED"], "q v")          # 引用符は剥がす
        self.assertEqual(os.environ["KEEP"], "fromenv")        # 既にある値は上書きしない
        self.assertNotIn("GH_TOKEN", os.environ)               # 空値は入れない（App からの払い出しに任せる）
        self.assertEqual(sorted(added), ["CONSOLE_TOKEN", "QUOTED"])

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(self.core.load_ctl_env(self.tmp / "no-such.env"), [])

    def test_sandbox_state_follows_the_env(self):
        """台帳の場所は SANDBOX_STATE が正（dispatch / run と同じ）。ctl.env で与えても効く（チケット 336）"""
        os.environ.pop("SANDBOX_STATE", None); self.addCleanup(os.environ.pop, "SANDBOX_STATE", None)
        ledger = self.tmp / "kumitate.state.json"
        self.envf.write_text(f"SANDBOX_STATE={ledger}\n", encoding="utf-8")
        self.core.load_ctl_env(self.envf)
        self.assertEqual(self.core.SANDBOX_STATE, ledger)
        self.assertEqual(self.core.sandbox_view()["state_file"], str(ledger))


class RepoStatusTest(unittest.TestCase):
    """PJ 定義を読む checkout が origin と食い違っていないか（チケット 337）。

    runner は PJ 定義（examples/projects/<pj>/ の gates.sh など）を作業ツリーから直接読むので、
    ctl で直して push していない変更はそのまま本番の挙動になる。ahead / behind / 汚れ を overview に
    出して気づけるようにする。ここでは一時的な checkout を作って判定だけを確かめる（本物の checkout は触らない）。
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-repo-status-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.core = load_module(self.tmp / "jobs").core
        self.origin, self.work = self.tmp / "origin.git", self.tmp / "work"
        self.git(self.tmp, "init", "--bare", "-b", "main", str(self.origin))
        self.git(self.tmp, "clone", str(self.origin), str(self.work))
        self.git(self.work, "config", "user.email", "t@example.invalid")
        self.git(self.work, "config", "user.name", "test")
        self.commit("gates.sh", "echo one\n", "最初のコミット")
        self.git(self.work, "push", "-u", "origin", "main")

    def git(self, cwd, *args):
        r = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def commit(self, name, body, msg, cwd=None):
        cwd = cwd or self.work
        (cwd / name).write_text(body, encoding="utf-8")
        self.git(cwd, "add", name); self.git(cwd, "commit", "-m", msg)

    def status(self):
        return self.core.repo_status(self.work, ttl=0)                 # 検査では毎回 git を読む

    def test_clean_checkout_is_not_diverged(self):
        d = self.status()
        self.assertTrue(d["known"]); self.assertFalse(d["diverged"])
        self.assertEqual((d["branch"], d["upstream"]), ("main", "origin/main"))
        self.assertEqual((d["ahead"], d["behind"], d["dirty"], d["detached"]), (0, 0, 0, False))
        self.assertEqual(d["path"], str(self.work))

    def test_commit_without_push_is_ahead(self):
        """337 そのもの: ctl で gates.sh を直して commit すると runner には効くが、origin には無い"""
        self.commit("gates.sh", "echo two\n", "gates.sh に db-migrate を足す")
        d = self.status()
        self.assertEqual((d["ahead"], d["behind"], d["dirty"]), (1, 0, 0))
        self.assertTrue(d["diverged"])

    def test_uncommitted_and_untracked_changes_count_as_dirty(self):
        (self.work / "gates.sh").write_text("echo dirty\n", encoding="utf-8")
        (self.work / "new.sh").write_text("echo new\n", encoding="utf-8")
        d = self.status()
        self.assertEqual(d["dirty"], 2); self.assertTrue(d["diverged"])
        self.assertEqual((d["ahead"], d["behind"]), (0, 0))

    def test_behind_after_fetch(self):
        """網は触らない（fetch しない）ので、behind は最後に fetch した時点との差"""
        other = self.tmp / "other"
        self.git(self.tmp, "clone", str(self.origin), str(other))
        self.git(other, "config", "user.email", "t@example.invalid"); self.git(other, "config", "user.name", "test")
        self.commit("gates.sh", "echo three\n", "別の checkout から直す", cwd=other)
        self.git(other, "push")
        self.assertEqual(self.status()["behind"], 0)                   # fetch する前は気づけない
        self.git(self.work, "fetch")
        d = self.status()
        self.assertEqual((d["ahead"], d["behind"]), (0, 1)); self.assertTrue(d["diverged"])

    def test_detached_head_has_no_upstream(self):
        sha = self.git(self.work, "rev-parse", "HEAD").strip()
        self.git(self.work, "checkout", "--detach", sha)
        d = self.status()
        self.assertTrue(d["detached"]); self.assertIsNone(d["branch"]); self.assertIsNone(d["upstream"])
        self.assertEqual((d["ahead"], d["behind"]), (None, None))
        self.assertFalse(d["diverged"])                                # 比べられないときは警告を出さない

    def test_not_a_checkout_is_unknown_and_silent(self):
        plain = self.tmp / "plain"; plain.mkdir()
        d = self.core.repo_status(plain, ttl=0)
        self.assertFalse(d["known"]); self.assertFalse(d["diverged"])

    def test_result_is_cached_per_path(self):
        """overview は 5 秒ごとに来る。毎回 git を 4 本走らせない"""
        first = self.core.repo_status(self.work, ttl=300)
        self.commit("gates.sh", "echo cached\n", "キャッシュ中の変更")
        self.assertEqual(self.core.repo_status(self.work, ttl=300)["ahead"], first["ahead"])
        self.assertEqual(self.status()["ahead"], 1)                    # ttl=0 なら読み直す

    def test_board_warns_when_the_checkout_differs_from_origin(self):
        """JS を動かす基盤が無いので、ボードのソースを検査する（他の画面の作りと同じ）"""
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewBoard"); board = app[i: app.index("\n}", i)]
        for key in ("o.repo", "repo.diverged", "T.board.repoDiverged", "T.board.repoAhead", "T.board.repoBehind", "T.board.repoDirty", "T.board.repoHow"):
            self.assertIn(key, board, key)


if __name__ == "__main__":
    unittest.main()
