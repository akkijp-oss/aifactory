"""MCP サーバー（console/bin/mcp）のテスト。stdio で JSON-RPC を流し、tools / resources を叩く。本番の DB は触らない。

  python3 -m unittest discover -s console/tests -v
"""
import json, os, pathlib, shutil, subprocess, sys, tempfile, time, unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
MCP = REPO / "console" / "bin" / "mcp"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_console import seed_workspace, PJ


class McpClient:
    def __init__(self, env):
        self.p = subprocess.Popen([sys.executable, str(MCP)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True, bufsize=1)
        self.n = 0
    def call(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}, ensure_ascii=False) + "\n"); self.p.stdin.flush()
        line = self.p.stdout.readline()
        assert line, f"応答が無い: {self.p.stderr.read()[-800:]}"
        return json.loads(line)
    def send(self, method, params=None):
        """リクエストを書くだけ（応答は recv で受ける）。採番した id を返す"""
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}, ensure_ascii=False) + "\n"); self.p.stdin.flush()
        return self.n
    def recv(self):
        """応答を 1 行だけ受ける（届いた順）"""
        line = self.p.stdout.readline()
        assert line, f"応答が無い: {self.p.stderr.read()[-800:]}"
        return json.loads(line)
    def notify(self, method, params=None):
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}) + "\n"); self.p.stdin.flush()
    def tool(self, _name, **args):
        r = self.call("tools/call", {"name": _name, "arguments": args})
        assert "result" in r, r
        res = r["result"]; text = res["content"][0]["text"]
        return res["isError"], (json.loads(text) if not res["isError"] else text)
    def close(self):
        self.p.stdin.close(); self.p.wait(timeout=10)


class McpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-mcp-test-"))
        cls.ws = cls.tmp / "ws"; cls.ws.mkdir()
        seed_workspace(cls.ws)
        cls.env = {**os.environ, "AIFACTORY_WORKSPACE": str(cls.ws), "CONSOLE_JOBS": str(cls.tmp / "jobs")}
        cls.c = McpClient(cls.env)

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_01_initialize(self):
        r = self.c.call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
        self.assertEqual(r["result"]["protocolVersion"], "2025-03-26")                 # 相手の版に合わせる
        self.assertEqual(r["result"]["serverInfo"]["name"], "aifactory"); self.assertIn("tools", r["result"]["capabilities"])
        self.c.notify("notifications/initialized")
        self.assertEqual(self.c.call("ping")["result"], {})
        r = self.c.call("initialize", {"protocolVersion": "1999-01-01"}); self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")   # 未知なら最新

    def test_02_tools_list(self):
        tools = self.c.call("tools/list")["result"]["tools"]
        names = {t["name"] for t in tools}
        for n in ("overview", "ticket_list", "ticket_show", "ticket_new", "ticket_action", "ticket_run", "intake", "dispatch", "run_list", "run_show", "read_file", "sandbox_status", "job_wait", "job_stop", "computer_open", "computer_action", "computer_close"):
            self.assertIn(n, names)
        for t in tools: self.assertEqual(t["inputSchema"]["type"], "object"); self.assertTrue(t["description"])

    def test_03_reads(self):
        err, o = self.c.tool("overview"); self.assertFalse(err); self.assertIn("counts", o)
        err, t = self.c.tool("ticket_list", all=True); self.assertFalse(err); self.assertGreater(len(t["tickets"]), 0)
        tid = t["tickets"][0]["id"]
        err, d = self.c.tool("ticket_show", id=tid); self.assertFalse(err); self.assertIsNotNone(d["body"]); self.assertTrue(d["history"])
        err, msg = self.c.tool("ticket_show", id=999999); self.assertTrue(err); self.assertIn("見つかりません", msg)
        err, msg = self.c.tool("ticket_show"); self.assertTrue(err)                                   # id 無し
        err, r = self.c.tool("run_list", all=True); self.assertFalse(err); self.assertTrue(r["runs"])
        name = next(x["name"] for x in r["runs"] if x["kind"] == "v1")
        err, d = self.c.tool("run_show", name=name); self.assertFalse(err); self.assertIn("state.json", [f["name"] for f in d["files"]])
        path = next(f["path"] for f in d["files"] if f["name"] == "state.json")
        err, f = self.c.tool("read_file", path=path, tail=200); self.assertFalse(err); self.assertIn("text", f)
        err, msg = self.c.tool("read_file", path="sandbox/bin/sandbox"); self.assertTrue(err)
        err, msg = self.c.tool("read_file", path="../.ssh/id_rsa"); self.assertTrue(err)
        for n in ("sandbox_status", "job_list", "logs", "config"):
            err, _ = self.c.tool(n); self.assertFalse(err, n)

    def test_04_resources(self):
        rs = self.c.call("resources/list")["result"]["resources"]
        uris = [r["uri"] for r in rs]; self.assertIn("aifactory://board", uris); self.assertTrue(any(u.startswith("aifactory://ticket/") for u in uris))
        r = self.c.call("resources/read", {"uri": uris[-1]})["result"]["contents"][0]; self.assertTrue(r["text"].startswith("# "))
        self.assertIn("error", self.c.call("resources/read", {"uri": "aifactory://nope"}))

    def test_05_writes(self):
        err, r = self.c.tool("ticket_new", pj=PJ, kind="research", title="調査: mcp 書き込みテスト", body="x\n\n## 完了条件\n- y"); self.assertFalse(err, r)
        tid = r["id"]; self.assertIsInstance(tid, int)
        err, msg = self.c.tool("ticket_action", id=tid, action="block"); self.assertTrue(err)                  # メモ無し
        err, r = self.c.tool("ticket_action", id=tid, action="start", note="mcp test"); self.assertFalse(err, r)
        self.assertEqual(self.c.tool("ticket_show", id=tid)[1]["ticket"]["status"], "in_progress")
        err, r = self.c.tool("ticket_action", id=tid, action="reopen"); self.assertFalse(err)
        self.assertEqual(self.c.tool("ticket_show", id=tid)[1]["ticket"]["status"], "todo")
        err, msg = self.c.tool("sandbox_release", task="x"); self.assertTrue(err)
        err, r = self.c.tool("ticket_run", id=tid, dry_run=True); self.assertFalse(err, r); jid = r["job"]["id"]
        err, w = self.c.tool("job_wait", id=jid, timeout_s=120); self.assertFalse(err)
        self.assertIn(w["job"]["state"], ("done", "failed")); self.assertIn("dry-run 終了", w["log"]["text"])
        err, s = self.c.tool("job_show", id=jid, tail=50); self.assertFalse(err); self.assertLessEqual(len(s["log"]["text"]), 50)
        err, msg = self.c.tool("job_stop", id=jid); self.assertTrue(err)                                       # 既に終わっている
        self.assertEqual(self.c.tool("ticket_show", id=tid)[1]["ticket"]["status"], "todo")

    def test_06_unknown_method_and_bad_json(self):
        r = self.c.call("nope"); self.assertEqual(r["error"]["code"], -32601)
        self.c.p.stdin.write("not json\n"); self.c.p.stdin.flush(); r = json.loads(self.c.p.stdout.readline()); self.assertEqual(r["error"]["code"], -32700)
        r = self.c.call("tools/call", {"name": "nope"}); self.assertEqual(r["error"]["code"], -32602)

    def test_07_job_wait_does_not_block_others(self):
        """job_wait の待ちで他のツールを塞がない（別スレッドで待つ）。ADR-0028"""
        jid = "20260908-000000-fake"
        d = self.tmp / "jobs" / jid; d.mkdir(parents=True, exist_ok=True)
        meta = d / "meta.json"
        running = {"id": jid, "kind": "fake", "label": "fake", "cmd": ["true"], "ticket": None, "run_hint": None,
                   "pid": 1, "started": "2026-09-08T00:00:00+09:00", "finished": None, "rc": None, "state": "running"}
        meta.write_text(json.dumps(running, ensure_ascii=False), encoding="utf-8")
        (d / "log").write_text("$ fake\n", encoding="utf-8")

        t0 = time.time()
        wait_id = self.c.send("tools/call", {"name": "job_wait", "arguments": {"id": jid, "timeout_s": 8}})
        over_id = self.c.send("tools/call", {"name": "overview", "arguments": {}})

        first = self.c.recv()                                                # overview が先に返る
        self.assertEqual(first["id"], over_id, f"job_wait に塞がれた: {first}")
        self.assertLess(time.time() - t0, 4, "overview が job_wait の待ちに引きずられている")
        self.assertFalse(first["result"]["isError"]); self.assertIn("counts", json.loads(first["result"]["content"][0]["text"]))

        meta.write_text(json.dumps({**running, "state": "done", "rc": 0, "finished": "2026-09-08T00:00:05+09:00"}, ensure_ascii=False), encoding="utf-8")
        second = self.c.recv()                                               # timeout を待たず、終わった時点で返る
        self.assertEqual(second["id"], wait_id)
        self.assertEqual(json.loads(second["result"]["content"][0]["text"])["job"]["state"], "done")
        self.assertLess(time.time() - t0, 8, "job_wait が timeout まで待っている")

        # 上限を超える timeout_s を渡しても、終われば即返る（300 秒を実際に待たない）
        meta.write_text(json.dumps(running, ensure_ascii=False), encoding="utf-8")
        big_id = self.c.send("tools/call", {"name": "job_wait", "arguments": {"id": jid, "timeout_s": 999}})
        time.sleep(1.5)
        meta.write_text(json.dumps({**running, "state": "done", "rc": 0, "finished": "2026-09-08T00:00:05+09:00"}, ensure_ascii=False), encoding="utf-8")
        r = self.c.recv(); self.assertEqual(r["id"], big_id)
        self.assertEqual(json.loads(r["result"]["content"][0]["text"])["job"]["state"], "done")

    def test_08_job_wait_limits_documented(self):
        """既定 60 秒・上限 300 秒が tools/list の説明文に書いてある"""
        desc = next(t["description"] for t in self.c.call("tools/list")["result"]["tools"] if t["name"] == "job_wait")
        self.assertIn("既定 60", desc); self.assertIn("上限 300", desc)


if __name__ == "__main__":
    unittest.main()
