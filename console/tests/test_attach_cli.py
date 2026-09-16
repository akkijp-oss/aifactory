"""console/bin/attach（手元のファイルをコンソールの API へ直接送る CLI）のテスト（チケット 596。段階 1 = ADR-0091）。

  python3 -m unittest discover -s console/tests -p 'test_attach_cli.py' -v

- エンコーダ単体: encode_multipart が組んだものを console の parse_multipart がそのまま解ける（日本語・空白・境界に似たバイト・CR LF・0x00）
- 結合: CLI → POST /api/tickets/<id>/attach → 実体の sha256 が元と一致 / 一覧・履歴・MCP ticket_show に載る
- 出力の約束: stdout は JSON 1 行で中身も base64 も合言葉も含まず、ファイルの大きさに比例して長くならない
- 認証（CONSOLE_TOKEN）・失敗（無いチケット・上限超過・手元に無いファイル）・設定ファイル（mcp-remote.env）
本番のデータは触らない（一時ディレクトリを AIFACTORY_WORKSPACE にする）。
"""
import base64, hashlib, importlib.machinery, importlib.util, json, os, pathlib, shutil, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
CONSOLE = REPO / "console" / "bin" / "console"
ATTACH = REPO / "console" / "bin" / "attach"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_console import PJ, free_port, load_module, seed_workspace         # noqa: E402
from test_mcp import McpClient                                             # noqa: E402
from test_attachments_api import PNG                                       # noqa: E402

sys.path.insert(0, str(REPO / "lib"))
import aifactory_attachments as attachments                                # noqa: E402

TOKEN = "s3cret-attach-token"   # どの出力にも出てはいけない値（HTTP ヘッダに載るので ascii。test_console の AuthTest と同じ流儀）


def load_attach():
    """console/bin/attach をモジュールとして読む（load_module と同じ流儀。拡張子が無いので loader を指定する）"""
    spec = importlib.util.spec_from_loader("attach_mod", importlib.machinery.SourceFileLoader("attach_mod", str(ATTACH)))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


class EncodeTest(unittest.TestCase):
    """組んだ multipart を、受け口（console の parse_multipart）がそのまま解けること"""
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-attach-cli-enc-"))
        cls.attach = load_attach()
        cls.console = load_module(cls.tmp / "jobs")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def roundtrip(self, files):
        ctype, body = self.attach.encode_multipart(files)
        fields, got = self.console.parse_multipart(ctype, body)
        self.assertEqual(fields, {})
        return got

    def test_names_and_bytes_survive(self):
        files = [("画面 1.png", PNG), ("手 順 書.txt", "あいう\n".encode()), ("plain.bin", bytes(range(256)))]
        self.assertEqual(self.roundtrip(files), files)

    def test_bodies_that_look_like_a_boundary_or_headers_survive(self):
        ctype, _ = self.attach.encode_multipart([("x.bin", b"x")])
        b = ctype.split("boundary=")[1]
        nasty = (b"--" + b.encode() + b"\r\nContent-Disposition: form-data; name=\"files\"\r\n\r\n"
                 b"\x00\r\n--aifactory-\r\n\x7f\xff" + "境界".encode())
        got = self.roundtrip([("わな.bin", nasty), ("あと.bin", b"\r\n\r\n--\r\n")])
        self.assertEqual(got, [("わな.bin", nasty), ("あと.bin", b"\r\n\r\n--\r\n")])

    def test_the_boundary_is_redrawn_when_it_appears_in_a_body(self):
        """境界が中身に現れたら引き直す（中身は書き換えない）"""
        seen = []
        real = self.attach.secrets.token_hex

        def fake(n):
            h = real(n)
            seen.append(h)
            return "0" * 32 if len(seen) == 1 else h     # 1 回目はわざと中身とぶつける
        self.attach.secrets.token_hex = fake
        try:
            data = b"--aifactory-" + b"0" * 32 + b"\r\n"
            ctype, body = self.attach.encode_multipart([("ぶつかる.bin", data)])
        finally:
            self.attach.secrets.token_hex = real
        self.assertNotIn("boundary=aifactory-" + "0" * 32, ctype)
        self.assertEqual(self.console.parse_multipart(ctype, body)[1], [("ぶつかる.bin", data)])

    def test_quotes_and_control_characters_do_not_break_the_header(self):
        """ヘッダーを壊さないための最小限の逃がし。名前そのものの締めは保存側（lib の sanitize）が正本"""
        got = self.roundtrip([('a"b\\c\r\nd.txt', b"x")])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][1], b"x")
        self.assertEqual(got[0][0], 'a"b\\cd.txt')


class CliTest(unittest.TestCase):
    """CLI → HTTP → 添付の実体まで（合言葉つきの console を 1 本立てる）"""
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-attach-cli-"))
        cls.ws = cls.tmp / "ws"; cls.ws.mkdir()
        seed_workspace(cls.ws)
        cls.home = cls.tmp / "home"; cls.home.mkdir()     # 手元の設定ファイルを読み違えない（本物の ~/.config を見に行かせない）
        cls.port = free_port()
        cls.env = {**os.environ, "AIFACTORY_WORKSPACE": str(cls.ws), "CONSOLE_JOBS": str(cls.tmp / "jobs"), "CONSOLE_TOKEN": TOKEN}
        cls.proc = subprocess.Popen([sys.executable, str(CONSOLE), "--port", str(cls.port)], env=cls.env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.base = f"http://127.0.0.1:{cls.port}"
        for _ in range(50):
            try:
                cls.api("GET", "/api/overview"); break
            except Exception: time.sleep(0.1)
        else: raise RuntimeError("console が起動しない")
        cls.mcp_env = {**os.environ, "AIFACTORY_WORKSPACE": str(cls.ws), "CONSOLE_JOBS": str(cls.tmp / "jobs")}
        cls.c = McpClient(cls.mcp_env)
        cls.c.call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})

    @classmethod
    def tearDownClass(cls):
        cls.c.close()
        cls.proc.terminate(); cls.proc.wait(timeout=10)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---- 道具
    @classmethod
    def api(cls, method, path, body=None):
        """合言葉つきの console を Bearer で叩く（?token= は cookie を焼いて 302 するので API では使わない）"""
        headers = {"Authorization": f"Bearer {TOKEN}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode(); headers |= {"Content-Type": "application/json", "X-Console": "1"}
        req = urllib.request.Request(cls.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def cli(self, *args, token=TOKEN, url=None, conf=None, **kw):
        """CLI を子プロセスで走らせる。合言葉は環境変数だけで渡す（argv には決して載せない）"""
        env = {k: v for k, v in os.environ.items() if k not in ("CONSOLE_TOKEN", "AIFACTORY_CONSOLE_URL")}
        env["HOME"] = str(self.home); env["XDG_CONFIG_HOME"] = str(conf if conf is not None else self.home / ".config")
        if token: env["CONSOLE_TOKEN"] = token
        if url is not False: env["AIFACTORY_CONSOLE_URL"] = url or self.base
        return subprocess.run([sys.executable, str(ATTACH), *[str(a) for a in args]], env=env, capture_output=True, text=True, timeout=120, **kw)

    def new_ticket(self, title="調査: 添付 CLI のテスト用"):
        st, d = self.api("POST", "/api/tickets", {"pj": PJ, "kind": "research", "title": title, "body": "x\n\n## 完了条件\n- y"})
        self.assertEqual(st, 200, d)
        return d["id"]

    def stored(self, tid, name):
        return self.ws / "kanban" / "attachments" / str(tid) / name

    def names_on_disk(self, tid):
        """実体の置き場を直に見る（このプロセスの AIFACTORY_WORKSPACE は一時 ws ではないので lib を呼ばない）"""
        d = self.ws / "kanban" / "attachments" / str(tid)
        return sorted(f.name for f in d.iterdir()) if d.is_dir() else []

    # ---- 本体
    def test_upload_is_byte_identical_and_shows_up_everywhere(self):
        tid = self.new_ticket()
        blob = os.urandom(1024 * 1024)
        src_png = self.tmp / "画面 1.png"; src_png.write_bytes(PNG)
        src_bin = self.tmp / "でーた.bin"; src_bin.write_bytes(blob)
        r = self.cli(tid, src_png, src_bin)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual(d["id"], tid)
        self.assertEqual(d["added"], ["画面 1.png", "でーた.bin"])
        # 実体がバイト単位で一致する
        for src in (src_png, src_bin):
            p = self.stored(tid, src.name)
            self.assertTrue(p.is_file(), p)
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), hashlib.sha256(src.read_bytes()).hexdigest(), src.name)
        # 一覧・履歴（既存の経路 = kb attach を通っていること）
        st, t = self.api("GET", f"/api/tickets/{tid}")
        self.assertEqual(sorted(a["name"] for a in t["attachments"]), sorted(["画面 1.png", "でーた.bin"]))
        self.assertTrue(any(h["field"] == "attachment" and "画面 1.png" in (h["new"] or "") for h in t["history"]), t["history"])
        # runner への配布は置き場の実体（attachments.listing）を正本に運ぶ（ADR-0041 決定 4）ので、置き場の一致で代替して確かめる
        self.assertEqual(self.names_on_disk(tid), sorted(["でーた.bin", "画面 1.png"]))
        # MCP から見ても同じ
        err, show = self.c.tool("ticket_show", id=tid)
        self.assertFalse(err, show)
        self.assertEqual(sorted(a["name"] for a in show["attachments"]), sorted(["でーた.bin", "画面 1.png"]))

    def test_stdout_is_small_and_leaks_nothing(self):
        """正常出力は小さな JSON 1 行。中身・その base64・合言葉を含まず、ファイルの大きさに比例して伸びない"""
        small = self.tmp / "小.bin"; small.write_bytes(b"0123456789abcdefghij")
        big = self.tmp / "大.bin"; big.write_bytes(os.urandom(1024 * 1024))
        outs = []
        for src in (small, big):
            tid = self.new_ticket()
            r = self.cli(tid, src)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.count("\n"), 1, r.stdout)          # JSON 1 行
            self.assertEqual(json.loads(r.stdout)["added"], [src.name])
            body = src.read_bytes()
            self.assertNotIn(base64.b64encode(body).decode(), r.stdout)
            self.assertNotIn(body[:64].hex(), r.stdout)
            self.assertNotIn(TOKEN, r.stdout + r.stderr)
            outs.append(len(r.stdout))
        self.assertLess(abs(outs[0] - outs[1]), 64, outs)                 # 20 バイトと 1 MiB で長さが変わらない

    def test_the_token_comes_from_the_environment_and_never_from_argv(self):
        tid = self.new_ticket()
        src = self.tmp / "認証.txt"; src.write_bytes(b"x")
        r = self.cli(tid, src, token=None)                                # 合言葉なし → 401
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertIn("401", r.stderr)
        self.assertNotIn(TOKEN, r.stderr)
        r = self.cli(tid, src)                                            # 合言葉あり → 通る。どの出力にも出ない
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)
        self.assertNotIn("Authorization", r.stdout + r.stderr)

    def test_settings_fall_back_to_the_local_env_file(self):
        """環境変数が無ければ ~/.config/aifactory/mcp-remote.env（mcp-remote と同じ置き場）から読む"""
        tid = self.new_ticket()
        src = self.tmp / "設定.txt"; src.write_bytes(b"conf")
        conf = self.tmp / "conf"; (conf / "aifactory").mkdir(parents=True)
        (conf / "aifactory" / "mcp-remote.env").write_text(f"# 手元の設定\nAIFACTORY_CONSOLE_URL={self.base}\nCONSOLE_TOKEN=\"{TOKEN}\"\n", encoding="utf-8")
        r = self.cli(tid, src, token=None, url=False, conf=conf)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["added"], ["設定.txt"])
        self.assertNotIn(TOKEN, r.stdout + r.stderr)

    def test_the_same_file_twice_is_kept_as_two_attachments(self):
        """同じ名前で 2 回送っても上書きしない（保存側の _unique が `-2` を付ける）。CLI は手形を持たないので冪等ではない"""
        tid = self.new_ticket()
        src = self.tmp / "二回.txt"; src.write_bytes(b"ni")
        self.assertEqual(self.cli(tid, src).returncode, 0)
        r = self.cli(tid, src)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["added"], ["二回-2.txt"])

    def test_a_missing_or_irregular_local_file_stops_before_sending(self):
        tid = self.new_ticket()
        ok = self.tmp / "ある.txt"; ok.write_bytes(b"ok")
        r = self.cli(tid, ok, self.tmp / "ない.txt")      # 1 件でも読めなければ 1 件も送らない
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertIn("ファイルがありません", r.stderr)
        self.assertEqual(self.names_on_disk(tid), [])
        r = self.cli(tid, self.tmp)                       # ディレクトリ
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("通常のファイルではありません", r.stderr)

    def test_an_unknown_ticket_is_reported_without_a_traceback(self):
        src = self.tmp / "迷子.txt"; src.write_bytes(b"x")
        r = self.cli(999999, src)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_console_that_is_not_there_says_only_the_host(self):
        src = self.tmp / "不通.txt"; src.write_bytes(b"x")
        port = free_port()
        r = self.cli(1, src, url=f"http://127.0.0.1:{port}")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertIn(f"127.0.0.1:{port}", r.stderr)
        self.assertNotIn(TOKEN, r.stderr)

    def test_too_big_a_file_is_refused_by_the_server_not_by_the_cli(self):
        """上限の数値は lib が正本。CLI は事前に判定しないので、断るのは server（メッセージも server のもの）"""
        tid = self.new_ticket()
        src = self.tmp / "大きすぎ.bin"; src.write_bytes(b"\0" * (attachments.MAX_FILE + 1))
        try:
            r = self.cli(tid, src)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(r.stdout, "")
            self.assertIn("上限", r.stderr)
            self.assertEqual(self.names_on_disk(tid), [])
        finally:
            src.unlink()


if __name__ == "__main__":
    unittest.main()
