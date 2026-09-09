"""console / MCP / 画面からの添付のテスト（チケット 378。第 1 段は workflow/tests/test_attachments.py）。

  python3 -m unittest discover -s console/tests -p 'test_attachments_api.py' -v

- HTTP: multipart で添付 → チケット詳細の attachments に載る / 配信（画像は inline・他は必ずダウンロード・常に nosniff）/ detach / X-Console なし
- multipart のパーサ単体（日本語のファイル名・複数ファイル・欄との混在）
- ジョブ: /api/intake に multipart で送ると <job>/files/ に実体ができて cmd の --attach に並ぶ
- MCP: ticket_attach（base64 / path）・ticket_show の attachments[].path・read_file が画像を image で返す・ticket_detach・annotations
- 静的: app.js が添付の口（FormData・attach・attachments の img）を持っている
本番のデータは触らない（一時ディレクトリを AIFACTORY_WORKSPACE にする）。
"""
import base64, json, os, pathlib, re, shutil, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.parse, urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
CONSOLE = REPO / "console" / "bin" / "console"
STATIC = REPO / "console" / "static"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_console import PJ, Http, free_port, load_module, seed_workspace   # noqa: E402
from test_mcp import McpClient                                             # noqa: E402

sys.path.insert(0, str(REPO / "lib"))
import aifactory_attachments as attachments                                # noqa: E402

# 1x1 の png（実体のある画像。mimetypes は名前で判定するが、配信と base64 が壊れていないことも見たい）
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def multipart(parts):
    """parts: [(欄名, ファイル名 or None, バイト列)] → (Content-Type, 本体)"""
    b = "----aifactory-test-boundary"
    out = b""
    for name, filename, data in parts:
        out += f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\"".encode()
        if filename is not None: out += f"; filename=\"{filename}\"".encode("utf-8")
        out += b"\r\n\r\n" + data + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return f"multipart/form-data; boundary={b}", out


class AttachHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-attach-test-"))
        cls.ws = cls.tmp / "ws"; cls.ws.mkdir()
        seed_workspace(cls.ws)
        cls.port = free_port()
        cls.env = {**os.environ, "AIFACTORY_WORKSPACE": str(cls.ws), "CONSOLE_JOBS": str(cls.tmp / "jobs")}
        cls.proc = subprocess.Popen([sys.executable, str(CONSOLE), "--port", str(cls.port)], env=cls.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.http = Http(cls.base)
        for _ in range(50):
            try: cls.http.get("/api/overview"); break
            except Exception: time.sleep(0.1)
        else: raise RuntimeError("console が起動しない")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(timeout=10)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def new_ticket(self, title="調査: 添付のテスト用"):
        st, d = self.http.post("/api/tickets", {"pj": PJ, "kind": "research", "title": title, "body": "x\n\n## 完了条件\n- y"})
        self.assertEqual(st, 200, d); self.assertTrue(d.get("id"), d)
        return d["id"]

    def post_form(self, path, parts, header=True):
        ctype, body = multipart(parts)
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers={"Content-Type": ctype, **({"X-Console": "1"} if header else {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def raw_get(self, path):
        """本体とヘッダーを見る GET（添付の配信）"""
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as r: return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e: return e.code, dict(e.headers), e.read()

    def test_attach_shows_up_in_the_ticket_and_in_the_history(self):
        tid = self.new_ticket()
        st, d = self.post_form(f"/api/tickets/{tid}/attach", [("files", "画面.png", PNG), ("files", "メモ.txt", "あ\n".encode())])
        self.assertEqual(st, 200, d)
        self.assertEqual(d["added"], ["画面.png", "メモ.txt"])
        st, d = self.http.get(f"/api/tickets/{tid}")
        names = [a["name"] for a in d["attachments"]]
        self.assertEqual(names, ["メモ.txt", "画面.png"])
        png = [a for a in d["attachments"] if a["name"] == "画面.png"][0]
        self.assertEqual(png["type"], "image/png")
        self.assertEqual(png["size"], len(PNG))
        self.assertTrue(png["path"].endswith("attachments/%s/画面.png" % tid), png["path"])   # read_file に渡せるパス
        self.assertTrue(any(h["field"] == "attachment" and "画面.png" in (h["new"] or "") for h in d["history"]), d["history"])

    def test_names_with_spaces_come_back_whole(self):
        """入った名前は一覧の差分で取る（kb の出力を空白で割ると `画面 1.png` が `画面` に切れる）"""
        tid = self.new_ticket()
        st, d = self.post_form(f"/api/tickets/{tid}/attach", [("files", "画面 1.png", PNG), ("files", "手 順 書.txt", b"x")])
        self.assertEqual(st, 200, d)
        self.assertEqual(d["added"], ["画面 1.png", "手 順 書.txt"])
        st, t = self.http.get(f"/api/tickets/{tid}")
        self.assertEqual(sorted(a["name"] for a in t["attachments"]), sorted(d["added"]))
        st, d = self.http.post(f"/api/tickets/{tid}/detach", {"name": "画面 1.png"})    # 返した名前で消せる
        self.assertEqual(st, 200, d)
        self.assertEqual([a["name"] for a in d["attachments"]], ["手 順 書.txt"])

    def test_image_is_served_inline_and_other_types_are_downloaded(self):
        tid = self.new_ticket()
        self.post_form(f"/api/tickets/{tid}/attach", [("files", "図.png", PNG), ("files", "note.txt", b"hello")])
        st, h, body = self.raw_get(f"/api/tickets/{tid}/attachments/{urllib.parse.quote('図.png')}")
        self.assertEqual(st, 200)
        self.assertEqual(h["Content-Type"], "image/png")
        self.assertTrue(h["Content-Disposition"].startswith("inline"), h["Content-Disposition"])
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(body, PNG)
        st, h, body = self.raw_get(f"/api/tickets/{tid}/attachments/note.txt")
        self.assertEqual(st, 200)
        self.assertEqual(h["Content-Type"], "application/octet-stream")     # 上げられた HTML / SVG を実行させない
        self.assertTrue(h["Content-Disposition"].startswith("attachment"), h["Content-Disposition"])
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(body, b"hello")

    def test_unknown_or_path_like_names_are_404(self):
        tid = self.new_ticket()
        self.post_form(f"/api/tickets/{tid}/attach", [("files", "図.png", PNG)])
        for name in (urllib.parse.quote("ない.png"), urllib.parse.quote("../../etc/passwd"), urllib.parse.quote("a/図.png")):
            st, _, _ = self.raw_get(f"/api/tickets/{tid}/attachments/{name}")
            self.assertEqual(st, 404, name)

    def test_detach_removes_the_file(self):
        tid = self.new_ticket()
        self.post_form(f"/api/tickets/{tid}/attach", [("files", "消す.png", PNG)])
        st, d = self.http.post(f"/api/tickets/{tid}/detach", {"name": "消す.png"})
        self.assertEqual(st, 200, d)
        self.assertEqual(d["attachments"], [])
        st, d = self.http.post(f"/api/tickets/{tid}/detach", {"name": "消す.png"})
        self.assertEqual(st, 404, d)

    def test_attach_needs_the_console_header_and_multipart(self):
        tid = self.new_ticket()
        st, d = self.post_form(f"/api/tickets/{tid}/attach", [("files", "x.png", PNG)], header=False)
        self.assertEqual(st, 403, d)
        st, d = self.http.post(f"/api/tickets/{tid}/attach", {"name": "x.png"})   # JSON では受けない
        self.assertEqual(st, 400, d)

    def test_intake_multipart_writes_the_files_and_passes_attach(self):
        ctype, body = multipart([("text", None, "画面が変です\n".encode()), ("files", "証拠 1.png", PNG)])
        req = urllib.request.Request(self.base + "/api/intake", data=body, method="POST",
                                     headers={"Content-Type": ctype, "X-Console": "1"})
        with urllib.request.urlopen(req, timeout=20) as r: d = json.loads(r.read())
        cmd = d["job"]["cmd"]
        self.assertIn("--attach", cmd)
        saved = cmd[cmd.index("--attach") + 1]
        self.assertTrue(saved.endswith("/files/証拠 1.png"), cmd)
        self.assertEqual(pathlib.Path(saved).read_bytes(), PNG)
        # 起票そのものは claude を呼ぶので、ここでは止めておく（ジョブが立ったことと引数だけを見る）
        self.http.post(f"/api/jobs/{d['job']['id']}/stop", {})


class ParseMultipartTest(unittest.TestCase):
    """bin/console の parse_multipart 単体（ブラウザーの FormData と curl -F の形が読めること）"""
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-attach-unit-"))
        cls.m = load_module(cls.tmp / "jobs")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_fields_and_files_with_japanese_names(self):
        ctype, body = multipart([("text", None, "本文\n".encode()), ("dry_run", None, b"1"),
                                 ("files", "画面 1.png", PNG), ("files", "表.csv", "a,b\n".encode())])
        fields, files = self.m.parse_multipart(ctype, body)
        self.assertEqual(fields, {"text": "本文\n", "dry_run": "1"})
        self.assertEqual([n for n, _ in files], ["画面 1.png", "表.csv"])
        self.assertEqual(files[0][1], PNG)

    def test_empty_file_input_is_not_counted(self):
        """ファイルを選ばずに送ると、ブラウザーは名前が空の part を出す"""
        ctype, body = multipart([("files", "", b"")])
        fields, files = self.m.parse_multipart(ctype, body)
        self.assertEqual(files, [])

    def test_not_multipart_is_refused(self):
        with self.assertRaises(self.m.ApiError):
            self.m.parse_multipart("multipart/form-data; boundary=zzz", b"not a multipart body")


class AttachMcpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-attach-mcp-"))
        cls.ws = cls.tmp / "ws"; cls.ws.mkdir()
        cls.tid = seed_workspace(cls.ws)
        cls.env = {**os.environ, "AIFACTORY_WORKSPACE": str(cls.ws), "CONSOLE_JOBS": str(cls.tmp / "jobs")}
        cls.c = McpClient(cls.env)
        cls.c.call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_attach_base64_then_show_then_read_file_returns_an_image(self):
        err, d = self.c.tool("ticket_attach", id=self.tid, name="画面.png", content_base64=base64.b64encode(PNG).decode())
        self.assertFalse(err, d)
        self.assertEqual(d["added"], ["画面.png"])
        err, d = self.c.tool("ticket_show", id=self.tid)
        self.assertFalse(err, d)
        item = [a for a in d["attachments"] if a["name"] == "画面.png"][0]
        self.assertEqual(item["type"], "image/png")
        # read_file は画像を image ブロックで返す（text はメタだけ）
        r = self.c.call("tools/call", {"name": "read_file", "arguments": {"path": item["path"]}})
        content = r["result"]["content"]
        self.assertEqual([c["type"] for c in content], ["text", "image"])
        self.assertEqual(content[1]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(content[1]["data"]), PNG)
        self.assertNotIn("base64", json.loads(content[0]["text"]))     # 中身を text に二重で載せない

    def test_attach_rejects_bad_base64_and_both_or_neither(self):
        err, msg = self.c.tool("ticket_attach", id=self.tid, name="x.png", content_base64="これは base64 ではない")
        self.assertTrue(err); self.assertIn("base64", msg)
        err, msg = self.c.tool("ticket_attach", id=self.tid, name="x.png")
        self.assertTrue(err)
        err, msg = self.c.tool("ticket_attach", id=self.tid, name="x.png", content_base64="AAAA", path="/tmp/x.png")
        self.assertTrue(err)

    def test_attach_path_allows_tmp_and_refuses_dot_directories(self):
        src = pathlib.Path(tempfile.mkdtemp(dir="/tmp", prefix="aifactory-attach-src-")) / "資料.png"
        src.write_bytes(PNG)
        err, d = self.c.tool("ticket_attach", id=self.tid, path=str(src))
        self.assertFalse(err, d)
        self.assertEqual(d["added"], ["資料.png"])
        err, msg = self.c.tool("ticket_attach", id=self.tid, path=str(pathlib.Path.home() / ".ssh" / "id_ed25519"))
        self.assertTrue(err); self.assertIn("`.` で始まる", msg)
        err, msg = self.c.tool("ticket_attach", id=self.tid, path="/etc/passwd")
        self.assertTrue(err); self.assertIn("ホームディレクトリ", msg)
        err, msg = self.c.tool("ticket_attach", id=self.tid, path="tmp/x.png")
        self.assertTrue(err); self.assertIn("絶対パス", msg)
        shutil.rmtree(src.parent, ignore_errors=True)

    def test_detach_removes_it(self):
        self.c.tool("ticket_attach", id=self.tid, name="いらない.png", content_base64=base64.b64encode(PNG).decode())
        err, d = self.c.tool("ticket_detach", id=self.tid, name="いらない.png")
        self.assertFalse(err, d)
        self.assertNotIn("いらない.png", [a["name"] for a in d["attachments"]])
        err, msg = self.c.tool("ticket_detach", id=self.tid, name="いらない.png")
        self.assertTrue(err)

    def test_tools_list_has_the_two_tools_with_annotations(self):
        r = self.c.call("tools/list")
        tools = {t["name"]: t for t in r["result"]["tools"]}
        self.assertIn("ticket_attach", tools); self.assertIn("ticket_detach", tools)
        self.assertIs(tools["ticket_attach"]["annotations"]["readOnlyHint"], False)
        self.assertIs(tools["ticket_detach"]["annotations"]["destructiveHint"], True)   # ファイルが消える
        self.assertIn("image", tools["read_file"]["description"])

    def test_large_images_are_refused_with_a_way_out(self):
        """base64 にすると 1.33 倍に膨らむので、上限を超える画像は読ませずに案内する"""
        sys.path.insert(0, str(REPO / "console" / "lib"))
        big = self.ws / "runs" / "big.png"
        big.parent.mkdir(parents=True, exist_ok=True)
        big.write_bytes(b"\x89PNG" + b"0" * (4 * 1024 * 1024 + 1))
        err, msg = self.c.tool("read_file", path=str(big))
        self.assertTrue(err)
        self.assertIn("大きすぎ", msg)


class StaticTest(unittest.TestCase):
    """画面（app.js / style.css）が添付の口を持っているか。ブラウザーを立てずに読める分だけ検査する"""
    def setUp(self):
        self.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_app_js_posts_multipart_and_shows_thumbnails(self):
        self.assertIn("FormData", self.js)
        self.assertRegex(self.js, r"tickets/\$\{[^}]+\}/attach")
        self.assertRegex(self.js, r"tickets/\$\{[^}]+\}/detach")
        self.assertRegex(self.js, r"/api/tickets/\$\{[^}]+\}/attachments/")   # 添付の配信の口
        self.assertRegex(self.js, r"<img class=\"thumb\"")                     # 画像はサムネイルで出す
        self.assertIn("dropzone", self.js)                          # 落として添付できる
        self.assertIn("data:", self.js)                             # run 画面のファイルの画像（base64）

    def test_style_has_the_attachment_classes(self):
        css = (STATIC / "style.css").read_text(encoding="utf-8")
        for cls in (".dropzone", ".attach-grid", ".thumb"):
            self.assertIn(cls, css)


if __name__ == "__main__":
    unittest.main()
