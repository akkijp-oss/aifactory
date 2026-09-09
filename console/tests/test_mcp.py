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
        self.p.stdout.close(); self.p.stderr.close()   # 1 テストで何本も立てるので、読み終わったパイプは閉じる


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
        for n in ("overview", "ticket_list", "ticket_show", "ticket_new", "ticket_action", "ticket_run", "intake", "dispatch", "run_list", "run_show", "run_action", "read_file", "sandbox_status", "job_wait", "job_stop", "computer_open", "computer_action", "computer_close"):
            self.assertIn(n, names)
        # 人間の後始末を書く口（チケット 335）。close / note の別と、決着（done / abandoned）が schema から読める
        ra = next(t for t in tools if t["name"] == "run_action")["inputSchema"]["properties"]
        self.assertEqual(ra["action"]["enum"], ["close", "note"]); self.assertEqual(ra["result"]["enum"], ["done", "abandoned"])
        for t in tools: self.assertEqual(t["inputSchema"]["type"], "object"); self.assertTrue(t["description"])
        # 対応キー名が説明に載っていること（チケット 344）。載っていないと agent が推測で送る
        ca = next(t for t in tools if t["name"] == "computer_action")["description"]
        for key in ("=", "F1", "ESC", "CMD"): self.assertIn(key, ca)

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
        err, r = self.c.tool("ticket_action", id=tid, action="append", text="mcp 補足", section="PM 補足"); self.assertFalse(err, r)
        body = self.c.tool("ticket_show", id=tid)[1]["body"]
        self.assertIn("## PM 補足", body); self.assertIn("mcp 補足", body)
        err, msg = self.c.tool("ticket_action", id=tid, action="append"); self.assertTrue(err)                 # 本文無しの追記
        err, r = self.c.tool("ticket_action", id=tid, action="set", note="消す前"); self.assertFalse(err, r)
        err, r = self.c.tool("ticket_action", id=tid, action="set", note=""); self.assertFalse(err, r)
        self.assertIn(self.c.tool("ticket_show", id=tid)[1]["ticket"]["note"], (None, ""))                     # 空文字列でメモを消せる
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

    def test_09_sync_is_dry_by_default(self):
        """MCP の sync は既定で書かない（LLM が完了済みチケットを過去の run で巻き戻せないように）。

        console の同じ操作は下見（kb sync --dry-run）と警告つきダイアログを通るが、MCP には確認の場が無い。
        既定を「前後を返すだけ」にして、書くのは dry_run: false を明示したときだけにする。"""
        err, r = self.c.tool("ticket_new", pj=PJ, kind="research", title="調査: mcp sync の下見", body="x\n\n## 完了条件\n- y")
        self.assertFalse(err, r); tid = r["id"]
        name = f"2020-01-01-{PJ}-{tid}"
        d = self.ws / "runs" / name; d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(json.dumps({"pj": PJ, "task": tid, "workflow": "research", "started": "2000-01-01T00:00:00+00:00",
                                                  "finished": "2000-01-01T00:00:00+00:00", "result": "end", "pr_url": "", "history": []},
                                                 ensure_ascii=False), encoding="utf-8")
        before = self.c.tool("ticket_show", id=tid)[1]

        err, p = self.c.tool("ticket_action", id=tid, action="sync", run=name)
        self.assertFalse(err, p)
        self.assertTrue(p["dry_run"], "MCP の sync が既定で書き込んでいる")
        self.assertEqual(p["before"]["status"], "todo"); self.assertEqual(p["after"]["status"], "done")
        self.assertTrue(p["updated_after_run"], "run の後にチケットが更新されていることを伝えていない")
        self.assertTrue(p.get("warning"), "上書きになる警告が無い")
        after = self.c.tool("ticket_show", id=tid)[1]
        self.assertEqual(after["ticket"]["status"], "todo", "下見なのにチケットが書き換わった")
        self.assertEqual(len(after["history"]), len(before["history"]))

        err, w = self.c.tool("ticket_action", id=tid, action="sync", run=name, dry_run=False)
        self.assertFalse(err, w)
        self.assertFalse(w["dry_run"]); self.assertEqual(w["before"]["status"], "todo"); self.assertEqual(w["after"]["status"], "done")
        self.assertTrue(w.get("warning")); self.assertIn("stdout", w)
        self.assertEqual(self.c.tool("ticket_show", id=tid)[1]["ticket"]["status"], "done")

    def test_10_ticket_show_carries_the_sync_preview(self):
        """run のあるチケットは、状態を合わせたらどうなるかを ticket_show の時点で見せる（HTTP の sync-preview と同じ内容）"""
        err, t = self.c.tool("ticket_list", all=True); self.assertFalse(err)
        tid = next(x["id"] for x in t["tickets"] if x.get("run"))
        err, d = self.c.tool("ticket_show", id=tid); self.assertFalse(err, d)
        self.assertIsNotNone(d.get("sync_preview"), "run のあるチケットに下見が付いていない")
        self.assertEqual(d["sync_preview"]["run"], d["ticket"]["run"])
        err, r = self.c.tool("ticket_new", pj=PJ, kind="research", title="調査: run の無いチケット", body="x\n\n## 完了条件\n- y")
        self.assertFalse(err, r)
        err, d = self.c.tool("ticket_show", id=r["id"]); self.assertFalse(err, d)
        self.assertIsNone(d["sync_preview"], "run が無いのに下見が付いている")

    def test_11_sync_dry_run_is_in_the_schema(self):
        tools = {t["name"]: t for t in self.c.call("tools/list")["result"]["tools"]}
        props = tools["ticket_action"]["inputSchema"]["properties"]
        self.assertIn("dry_run", props, "ticket_action のスキーマに dry_run が無い（呼び手が書く方法を見つけられない）")
        self.assertIn("sync", props["dry_run"].get("description", ""))

    def test_12_ticket_run_can_restart_from_a_step(self):
        """human で止まった run を続きから回す口が MCP から見えること（チケット 333）。
        説明に「打つべき値がどこに出るか」まで書く（呼び手は step 名を推し測れない）"""
        props = {t["name"]: t for t in self.c.call("tools/list")["result"]["tools"]}["ticket_run"]["inputSchema"]["properties"]
        for k in ("from_step", "from_branch"):
            self.assertIn(k, props, f"ticket_run のスキーマに {k} が無い（呼び手が続きから回せない）")
            self.assertTrue(props[k].get("description"))
        self.assertIn("ticket_show", props["from_step"]["description"])
        self.assertIn("wip", props["from_branch"]["description"])

    def test_13_job_wait_does_not_block_ticket_show(self):
        """job_wait の待ちで ticket_show が塞がれない（チケット 336 の 1 番目。PM は 2026-09-08 に塞がれた）。

        ticket_show は sync_preview のために kb を 1 回起動するので、「別スレッドに逃がす」だけでなく
        「重い読み取りでも 2 秒以内に返る」ことまで固定する。
        """
        jid = "20260908-000001-fake"
        d = self.tmp / "jobs" / jid; d.mkdir(parents=True, exist_ok=True)
        meta = d / "meta.json"
        running = {"id": jid, "kind": "fake", "label": "fake", "cmd": ["true"], "ticket": None, "run_hint": None,
                   "pid": 1, "started": "2026-09-08T00:00:00+09:00", "finished": None, "rc": None, "state": "running"}
        meta.write_text(json.dumps(running, ensure_ascii=False), encoding="utf-8")
        (d / "log").write_text("$ fake\n", encoding="utf-8")

        # sync_preview（kb を 1 回起動する重い読み取り）まで通るチケットを自前で用意する
        err, r = self.c.tool("ticket_new", pj=PJ, kind="research", title="調査: job_wait 中の ticket_show", body="x\n\n## 完了条件\n- y")
        self.assertFalse(err, r); tid = r["id"]
        name = f"2020-01-01-{PJ}-{tid}"
        rd = self.ws / "runs" / name; rd.mkdir(parents=True, exist_ok=True)
        (rd / "state.json").write_text(json.dumps({"pj": PJ, "task": tid, "workflow": "research", "started": "2000-01-01T00:00:00+00:00",
                                                   "finished": "2000-01-01T00:00:00+00:00", "result": "end", "pr_url": "", "history": []},
                                                  ensure_ascii=False), encoding="utf-8")
        err, r = self.c.tool("ticket_action", id=tid, action="set", run=name); self.assertFalse(err, r)

        wait_id = self.c.send("tools/call", {"name": "job_wait", "arguments": {"id": jid, "timeout_s": 8}})
        t0 = time.time()
        show_id = self.c.send("tools/call", {"name": "ticket_show", "arguments": {"id": tid}})

        first = self.c.recv()
        self.assertEqual(first["id"], show_id, f"job_wait に塞がれた: {first}")
        self.assertLess(time.time() - t0, 2, "ticket_show が 2 秒以内に返らない（336 の完了条件）")
        self.assertFalse(first["result"]["isError"])
        d2 = json.loads(first["result"]["content"][0]["text"])
        self.assertIn("ticket", d2); self.assertIsNotNone(d2.get("sync_preview"))

        meta.write_text(json.dumps({**running, "state": "done", "rc": 0, "finished": "2026-09-08T00:00:05+09:00"}, ensure_ascii=False), encoding="utf-8")
        second = self.c.recv()
        self.assertEqual(second["id"], wait_id)
        self.assertEqual(json.loads(second["result"]["content"][0]["text"])["job"]["state"], "done")

    def test_14_tools_carry_annotations(self):
        """tools/list に annotations を載せる（336）。

        Claude Code は readOnlyHint の無いツールを「並列に呼べない」とみなして同じターンの呼び出しを直列に送る。
        サーバーが job_wait を別スレッドに逃がしていても（ADR-0028）、これが無いと呼び手の側で塞がる。
        """
        tools = {t["name"]: t for t in self.c.call("tools/list")["result"]["tools"]}
        for n, t in tools.items():
            self.assertIn("annotations", t, f"{n} に annotations が無い")
            self.assertIn("readOnlyHint", t["annotations"], n)
            self.assertTrue(t["annotations"].get("title"), n)
        for n in ("ticket_show", "overview", "job_show", "job_wait", "sandbox_status", "run_show", "read_file", "keys_list"):
            self.assertTrue(tools[n]["annotations"]["readOnlyHint"], f"{n} は読み取りのはず")
        for n in ("ticket_run", "ticket_new", "ticket_action", "run_action", "intake", "dispatch", "sandbox_ls", "sandbox_release", "job_stop"):
            self.assertFalse(tools[n]["annotations"]["readOnlyHint"], f"{n} は状態を変える")
        for n in ("sandbox_release", "job_stop"):
            self.assertTrue(tools[n]["annotations"].get("destructiveHint"), f"{n} は取り返しがつかない")

    # ---- sandbox_status（336 の 2・3 番目）。実機も sandbox/bin/sandbox も使わず、PATH に偽の `sandbox` を置いた別クライアントで確かめる
    LS_TABLE = ("TASK     VM             VMID   IP           STATUS    SINCE\n"
                "229      sb-kumitate-01 9204   10.77.1.4    running   2026-09-06T12:00:07+09:00\n"
                "-        sb-kumitate-long-name-99 9299 10.77.1.9 stopped\n")

    def client(self, tag, fake_sandbox=True, state=None):
        """別の jobs / HOME を持つ MCP クライアント。既存の cls.c の挙動は変えない。

        HOME も差し替えるのは、core.child_env() が ~/.local/bin を PATH の末尾に足すため（本物の sandbox を呼ばない）。
        """
        home = self.tmp / f"home-{tag}"; home.mkdir(parents=True, exist_ok=True)
        env = {**self.env, "CONSOLE_JOBS": str(self.tmp / f"jobs-{tag}"), "HOME": str(home),
               "SANDBOX_STATE": str(state or (home / "state.json"))}
        if fake_sandbox:
            b = self.tmp / f"bin-{tag}"; b.mkdir(parents=True, exist_ok=True)
            sb = b / "sandbox"
            sb.write_text('#!/bin/sh\n[ "$1" = ls ] || exit 2\ncat <<\'EOF\'\n' + self.LS_TABLE + 'EOF\n', encoding="utf-8")
            sb.chmod(0o755)
            env["PATH"] = f"{b}:{env.get('PATH', '')}"
        else:
            env["PATH"] = "/usr/bin:/bin"
        c = McpClient(env); self.addCleanup(c.close)
        return c

    def test_14b_keys_list_is_masked_and_read_only(self):
        """keys_list（#379）はマスク済みの一覧を返し、値は返さない。追加・削除は MCP に出さない"""
        home = self.tmp / "home-keys"; home.mkdir(parents=True, exist_ok=True)
        keys = home / "keys.json"; state = home / "state.json"
        token = "fake-token-mcp-test-4321"
        keys.write_text(json.dumps({"keys": [
            {"name": "fable-main", "token": token, "allow": {"fable": True, "other": False},
             "enabled": True, "note": "", "issued": "2026-09-09", "last_used": None, "uses": 0},
            {"name": "opus-off", "token": token, "allow": {"fable": False, "other": True},
             "enabled": False, "note": "", "issued": "2026-09-09", "last_used": "2026-09-09T10:00:00+09:00", "uses": 3}]},
            ensure_ascii=False), encoding="utf-8")
        state.write_text(json.dumps({"379": {"vmid": 9201, "name": "sb-t-01", "ip": "10.77.1.1", "pj": PJ,
                                             "since": "2026-09-09T10:00:00+09:00", "keys": {"fable": "fable-main"}}},
                                    ensure_ascii=False), encoding="utf-8")
        env = {**self.env, "CONSOLE_JOBS": str(self.tmp / "jobs-keys"), "HOME": str(home), "PATH": "/usr/bin:/bin",
               "SANDBOX_STATE": str(state), "SANDBOX_KEYS": str(keys)}
        c = McpClient(env); self.addCleanup(c.close)

        tools = {t["name"] for t in c.call("tools/list")["result"]["tools"]}
        self.assertIn("keys_list", tools)
        self.assertEqual(tools & {"keys_add", "keys_rm", "keys_set", "keys_token"}, set())   # 秘密を渡す口は出さない

        err, d = c.tool("keys_list"); self.assertFalse(err, d)
        self.assertNotIn(token, json.dumps(d, ensure_ascii=False))
        self.assertEqual([k["name"] for k in d["keys"]], ["fable-main", "opus-off"])
        self.assertEqual(d["keys"][0]["tail4"], "4321"); self.assertNotIn("token", d["keys"][0])
        self.assertEqual(d["keys"][0]["in_use"], ["379"]); self.assertEqual(d["keys"][1]["in_use"], [])
        self.assertEqual(d["candidates"], {"fable": 1, "other": 0})                          # 使わない設定の鍵は候補に数えない
        self.assertEqual(d["keys_file"], str(keys)); self.assertTrue(d["exists"])

        err, s = c.tool("sandbox_status"); self.assertFalse(err, s)
        self.assertEqual(s["leases"][0]["keys"], {"fable": "fable-main"})                    # 貸出行にも鍵の名前が出る

    def test_15_sandbox_status_refreshes_a_stale_ls(self):
        """`sandbox ls` が古ければ、sandbox_status が裏で取り直しを起こす（336 の 2 番目）"""
        c = self.client("15")
        err, d = c.tool("sandbox_status"); self.assertFalse(err, d)
        self.assertTrue(d["ls_refreshing"], "一度も ls を取っていないのに取り直しを起こしていない")
        jid = d["ls_refresh_job"]; self.assertTrue(jid)
        self.assertIsNone(d["ls_fetched"])                                   # 今回は古い値のまま返す（待たせない）

        err, w = c.tool("job_wait", id=jid, timeout_s=30); self.assertFalse(err, w)
        self.assertEqual(w["job"]["rc"], 0, w["log"]["text"][-400:])

        err, d = c.tool("sandbox_status"); self.assertFalse(err, d)
        self.assertFalse(d["ls_refreshing"]); self.assertFalse(d["ls_stale"])
        self.assertLess(d["ls_age_s"], 600)
        self.assertEqual([v["name"] for v in d["vms"]], ["sb-kumitate-01", "sb-kumitate-long-name-99"])
        self.assertEqual(next(p for p in d["templates"] if p["pj"] == PJ)["pool_actual"], 1)

        err, d = c.tool("sandbox_status"); self.assertFalse(err, d)           # 新しいうちは何度呼んでもジョブを増やさない
        err, jl = c.tool("job_list"); self.assertFalse(err)
        self.assertEqual(len([j for j in jl["jobs"] if j["kind"] == "sandbox-ls"]), 1)

    def test_15b_sandbox_status_says_why_it_could_not_refresh(self):
        """取り直しに失敗しても sandbox_status 自体は成功で返す（読めた分は返す）"""
        c = self.client("15b", fake_sandbox=False)
        err, d = c.tool("sandbox_status"); self.assertFalse(err, d)
        self.assertFalse(d["ls_refreshing"]); self.assertTrue(d.get("ls_refresh_error"))

    def test_16_sandbox_status_lent_comes_from_the_state_file(self):
        """貸出中 VM の IP を MCP から引ける（336 の 3 番目）。台帳は SANDBOX_STATE で差し替えられる"""
        state = self.tmp / "state-16.json"
        lease = {"vmid": 9204, "name": "sb-kumitate-01", "ip": "10.77.1.4", "pj": PJ,
                 "since": "2026-09-08T10:00:00+09:00", "phase": "ready"}
        state.write_text(json.dumps({"336": lease}, ensure_ascii=False), encoding="utf-8")
        c = self.client("16", fake_sandbox=False, state=state)

        err, d = c.tool("sandbox_status"); self.assertFalse(err, d)
        self.assertEqual(d["state_file"], str(state)); self.assertTrue(d["state_exists"]); self.assertIsNone(d["state_error"])
        self.assertEqual(d["lent"]["336"]["ip"], "10.77.1.4")
        self.assertEqual(d["leases"], [{"task": "336", "vmid": "9204", "name": "sb-kumitate-01", "ip": "10.77.1.4",
                                        "pj": PJ, "since": "2026-09-08T10:00:00+09:00", "phase": "ready",
                                        "url": d["urls"]["336"], "vm_status": None, "keys": None}])   # keys は鍵プールを使っていなければ null（379）

        state.unlink()
        err, d = c.tool("sandbox_status"); self.assertFalse(err, d)
        self.assertEqual(d["lent"], {}); self.assertEqual(d["leases"], [])
        self.assertFalse(d["state_exists"])

    # ---- run_wait / run_show の progress（チケット 340）
    def fake_run(self, tag, state, gates=None):
        """偽の run ディレクトリを 1 つ作り、名前と state.json のパスを返す"""
        name = f"2026-09-10-{PJ}-{tag}"
        d = self.ws / "runs" / name; d.mkdir(parents=True, exist_ok=True)
        st = d / "state.json"
        st.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        if gates is not None:
            (d / "work").mkdir(exist_ok=True); (d / "work" / "gates.txt").write_text(gates, encoding="utf-8")
        return name, st

    RUNNING = {"pj": PJ, "task": 340, "workflow": "feature", "started": "2026-09-01T10:00:00+09:00",
               "finished": None, "result": None, "pr_url": "", "next": "implement",
               "current": {"step": "implement", "kind": "agent", "log": "agent-implement-1.log", "since": "2026-09-01T10:05:00+09:00"},
               "history": [{"step": "plan", "ok": True, "next": "implement", "at": "2026-09-01T10:05:00+09:00"}]}

    def test_17_run_wait_does_not_block_others(self):
        """run_wait の待ちで他のツールを塞がない（job_wait と同じく別スレッドで待つ。ADR-0028）"""
        err, r = self.c.tool("ticket_new", pj=PJ, kind="research", title="調査: run_wait 中の ticket_show", body="x\n\n## 完了条件\n- y")
        self.assertFalse(err, r)
        name, _ = self.fake_run("917", self.RUNNING)
        t0 = time.time()
        wait_id = self.c.send("tools/call", {"name": "run_wait", "arguments": {"name": name, "timeout_s": 8}})
        show_id = self.c.send("tools/call", {"name": "ticket_show", "arguments": {"id": r["id"]}})

        first = self.c.recv()                                                # ticket_show が先に返る
        self.assertEqual(first["id"], show_id, f"run_wait に塞がれた: {first}")
        self.assertLess(time.time() - t0, 4, "ticket_show が run_wait の待ちに引きずられている")
        self.assertFalse(first["result"]["isError"])

        second = self.c.recv()                                               # run_wait は timeout まで待って返る
        self.assertEqual(second["id"], wait_id)
        self.assertFalse(json.loads(second["result"]["content"][0]["text"])["changed"])

    def test_18_run_wait_returns_when_a_step_finishes(self):
        """history が 1 件増えたら timeout を待たずに返る。返るのは構造化した要点だけで、ログ本文は含まない"""
        gates = "PASS lint\nFAIL unit (~/gates/unit.log)\n=== unit.log (tail 60)\nFAIL 混ぜてはいけないログ本文\n" + "x" * 5000 + "\n"
        name, st = self.fake_run("918", self.RUNNING, gates=gates)
        t0 = time.time()
        wait_id = self.c.send("tools/call", {"name": "run_wait", "arguments": {"name": name, "timeout_s": 30}})
        time.sleep(1.5)
        st.write_text(json.dumps({**self.RUNNING, "next": "review", "pr_url": "https://example.invalid/pr/1",
                                  "current": {"step": "review", "kind": "agent", "log": "agent-review-2.log", "since": "2026-09-01T10:20:00+09:00"},
                                  "history": self.RUNNING["history"] + [{"step": "gates", "ok": False, "next": "implement", "at": "2026-09-01T10:20:00+09:00"}]},
                                 ensure_ascii=False), encoding="utf-8")
        r = self.c.recv(); self.assertEqual(r["id"], wait_id)
        self.assertLess(time.time() - t0, 30, "run_wait が timeout まで待っている")
        text = r["result"]["content"][0]["text"]; d = json.loads(text)
        self.assertTrue(d["changed"]); self.assertEqual(d["status"], "running")
        for k in ("step", "ok", "next", "gate_fails", "pr_url", "result"): self.assertIn(k, d)
        self.assertEqual(d["step"], "review"); self.assertIs(d["ok"], False); self.assertEqual(d["next"], "review")
        self.assertEqual(d["gate_fails"], ["unit"]); self.assertEqual(d["pr_url"], "https://example.invalid/pr/1")
        self.assertIsNone(d["result"])
        self.assertNotIn("log", d)                                           # ログ本文は返さない（read_file で読む）
        self.assertLess(len(text), 4000, "run_wait の戻りが大きすぎる（ログ本文が混ざっていないか）")
        self.assertNotIn("混ぜてはいけないログ本文", text)

    def test_19_run_wait_returns_running_on_timeout(self):
        """何も動かなければ timeout_s ぶん待って changed: false / running のまま返る"""
        name, _ = self.fake_run("919", self.RUNNING)
        t0 = time.time()
        err, d = self.c.tool("run_wait", name=name, timeout_s=2); self.assertFalse(err, d)
        self.assertGreaterEqual(time.time() - t0, 1.5); self.assertLess(time.time() - t0, 10)
        self.assertFalse(d["changed"]); self.assertEqual(d["status"], "running"); self.assertEqual(d["step"], "implement")
        err, msg = self.c.tool("run_wait", name="2026-01-01-nope-1", timeout_s=1); self.assertTrue(err); self.assertIn("見つかりません", msg)
        err, msg = self.c.tool("run_wait", name=name, timeout_s=-1); self.assertTrue(err)
        err, msg = self.c.tool("run_wait", name=name, until="nope", timeout_s=1); self.assertTrue(err)

    def test_19b_run_wait_ignores_a_half_written_state_file(self):
        """書いている途中の state.json（runner の save は tmp+rename ではない）を読めなかった周回を、
           「工程が変わった」と誤判定しない"""
        name, st = self.fake_run("921", self.RUNNING)
        whole = st.read_text(encoding="utf-8")
        t0 = time.time()
        wait_id = self.c.send("tools/call", {"name": "run_wait", "arguments": {"name": name, "timeout_s": 8}})
        time.sleep(1)
        st.write_text(whole[:40], encoding="utf-8")                          # 途中まで（壊れた JSON）
        time.sleep(2.5)
        st.write_text(whole, encoding="utf-8")                               # 中身は同じまま書き直す
        r = self.c.recv(); self.assertEqual(r["id"], wait_id)
        d = json.loads(r["result"]["content"][0]["text"])
        self.assertGreaterEqual(time.time() - t0, 7, "壊れた JSON を掴んだ周回で返ってしまっている")
        self.assertFalse(d["changed"]); self.assertEqual(d["status"], "running"); self.assertEqual(d["step"], "implement")

    def test_20_run_show_carries_progress(self):
        """run_show の progress に工程ごとの経過秒・今の工程の経過秒・ゲートの PASS/FAIL/INFO 一覧が入る"""
        gates = "PASS lint\nFAIL unit (~/gates/unit.log)\nINFO typecheck red (also red on base; not a gate)\n=== unit.log (tail 60)\nPASS 拾ってはいけない\n"
        name, _ = self.fake_run("920", self.RUNNING, gates=gates)
        err, d = self.c.tool("run_show", name=name); self.assertFalse(err, d)
        pg = d["progress"]
        self.assertEqual([h["elapsed_s"] for h in pg["history"]], [300])      # started 10:00 → plan 終了 10:05
        self.assertEqual(pg["current"]["step"], "implement"); self.assertIsInstance(pg["current"]["elapsed_s"], int)
        self.assertIsInstance(pg["elapsed_s"], int)
        self.assertEqual([(g["name"], g["status"]) for g in pg["gates"]],
                         [("lint", "PASS"), ("unit", "FAIL"), ("typecheck", "INFO")])   # `=== ` から先は読まない

    def test_21_run_wait_limits_and_shape_documented(self):
        """既定 60 秒・上限 300 秒と「ログ本文を返さない」ことが tools/list の説明文から読める"""
        tools = {t["name"]: t for t in self.c.call("tools/list")["result"]["tools"]}
        self.assertIn("run_wait", tools)
        desc = tools["run_wait"]["description"]
        self.assertIn("既定 60", desc); self.assertIn("上限 300", desc); self.assertIn("ログ本文は含まない", desc)
        self.assertEqual(tools["run_wait"]["inputSchema"]["properties"]["until"]["enum"], ["step", "result"])
        self.assertTrue(tools["run_wait"]["annotations"]["readOnlyHint"], "run_wait は待つだけの読み取り")
        self.assertIn("progress", tools["run_show"]["description"])
        # 運転の型（ticket_run → run_wait → run_show / read_file）を instructions に書く
        ins = self.c.call("initialize", {"protocolVersion": "2025-03-26"})["result"]["instructions"]
        for k in ("ticket_run", "run_wait", "run_show", "read_file"): self.assertIn(k, ins)


if __name__ == "__main__":
    unittest.main()
