"""鍵プールの残量観測（lib/aifactory_keys_quota.py。ADR-0087）:

- 応答ヘッダ `anthropic-ratelimit-unified-*` を 3 つの窓（5h / 7d / 7d_oi）として読む。7d_oi は Fable で叩いたときだけ返る
- 429 はヘッダ付きの成功として読み、それ以外の 4xx / 5xx は理由（error.type）だけを残す。鍵の値はどこにも出ない
- 安いモデルの回（7d_oi 無し）が Fable の回の 7d_oi を消さない（据え置き）
- 履歴は観測できた窓ごとに 1 行、窓の始点（reset − 窓長・残量 100%）は (鍵, 窓, reset) につき 1 行だけ合成する
- 1 周（run_probe）: Fable 許可の鍵は前回の全窓観測から間隔が空いたときだけ Fable で叩く。無効な鍵は飛ばし、値が入れ替わった鍵は履歴を捨てる
- 要約（summarize）: 残量% / いちばん逼迫している窓 / ペースからの枯渇予測 / リセットを過ぎた窓は逼迫の判定から外す

  python3 -m unittest discover -s console/tests -p 'test_keys_quota.py' -v
"""
import contextlib, io, json, os, pathlib, shutil, sys, tempfile, unittest, urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "lib"))
import aifactory_keys_quota as kq   # noqa: E402
REAL_PROBE = kq.probe_key   # CLI のテストで kq.probe_key を差し替えても本物を呼べるように

NOW = 1_787_000_000.0
RESET_5H = 1_787_010_000        # now + 10000 s（窓長 18000 s なので経過 8000 s = 44%）
RESET_7D = 1_787_400_000        # now + 400000 s
FABLE_HEADERS = {
    "anthropic-ratelimit-unified-5h-utilization": "0.23", "anthropic-ratelimit-unified-5h-reset": str(RESET_5H), "anthropic-ratelimit-unified-5h-status": "allowed",
    "anthropic-ratelimit-unified-7d-utilization": "0.59", "anthropic-ratelimit-unified-7d-reset": str(RESET_7D), "anthropic-ratelimit-unified-7d-status": "allowed",
    "anthropic-ratelimit-unified-7d_oi-utilization": "0.54", "anthropic-ratelimit-unified-7d_oi-reset": str(RESET_7D), "anthropic-ratelimit-unified-7d_oi-status": "allowed",
    "anthropic-ratelimit-unified-representative-claim": "five_hour", "anthropic-ratelimit-unified-overage-status": "rejected",
    "anthropic-ratelimit-unified-overage-disabled-reason": "org_level_disabled", "anthropic-ratelimit-unified-status": "allowed",
}
CHEAP_HEADERS = {k: v for k, v in FABLE_HEADERS.items() if "7d_oi" not in k}


class FakeResponse:
    def __init__(self, status, headers): self.status, self.headers = status, headers
    def __enter__(self): return self
    def __exit__(self, *a): return False


def responder(status=200, headers=None, *, error=None, body=b""):
    """probe_key の request= に渡す偽物。呼ばれた model を記録する"""
    calls = []

    def request(token, model, base_url, timeout):
        calls.append({"token": token, "model": model, "base_url": base_url})
        if error is not None: raise error
        if status >= 400:
            raise urllib.error.HTTPError(base_url, status, "x", headers or {}, io.BytesIO(body))
        return FakeResponse(status, headers or {})
    request.calls = calls
    return request


class ProbeTest(unittest.TestCase):
    def test_fable_headers_give_three_windows(self):
        s = kq.probe_key("tok", "claude-fable-5-1", request=responder(200, FABLE_HEADERS), now=NOW)
        self.assertIsNone(s["error"]); self.assertTrue(s["covers_all"])
        self.assertEqual(sorted(s["windows"]), ["5h", "7d", "7d_oi"])
        self.assertEqual(s["windows"]["7d_oi"], {"utilization": 0.54, "reset_ts": float(RESET_7D), "status": "allowed"})
        self.assertEqual(s["claim"], "five_hour"); self.assertEqual(s["overage_reason"], "org_level_disabled"); self.assertEqual(s["http_status"], 200)

    def test_cheap_model_has_no_oi_window(self):
        s = kq.probe_key("tok", "claude-haiku-4-5", request=responder(200, CHEAP_HEADERS), now=NOW)
        self.assertIsNone(s["error"]); self.assertFalse(s["covers_all"]); self.assertEqual(sorted(s["windows"]), ["5h", "7d"])

    def test_429_is_read_as_headers(self):
        h = {**CHEAP_HEADERS, "anthropic-ratelimit-unified-5h-utilization": "1.0", "anthropic-ratelimit-unified-5h-status": "rejected", "anthropic-ratelimit-unified-status": "rejected"}
        s = kq.probe_key("tok", "m", request=responder(429, h), now=NOW)
        self.assertIsNone(s["error"]); self.assertEqual(s["http_status"], 429)
        self.assertEqual(s["windows"]["5h"]["status"], "rejected"); self.assertEqual(s["status"], "rejected")

    def test_401_keeps_only_the_reason(self):
        body = json.dumps({"type": "error", "error": {"type": "authentication_error", "message": "token sk-ant-oat01-secret is bad"}}).encode()
        s = kq.probe_key("sk-ant-oat01-secret", "m", request=responder(401, {}, body=body), now=NOW)
        self.assertEqual(s["error"], "HTTP 401 authentication_error"); self.assertEqual(s["windows"], {}); self.assertFalse(s["covers_all"])
        self.assertNotIn("secret", json.dumps(s))

    def test_network_error_and_missing_headers(self):
        s = kq.probe_key("tok", "m", request=responder(error=urllib.error.URLError("unreachable")), now=NOW)
        self.assertTrue(s["error"].startswith("network:"))
        s = kq.probe_key("tok", "m", request=responder(200, {}), now=NOW)
        self.assertEqual(s["error"], "no-ratelimit-headers")

    def test_request_shape_is_oauth_messages(self):
        seen = {}

        class Opener:
            def __call__(self, req, timeout):
                seen["url"] = req.full_url; seen["headers"] = {k.lower(): v for k, v in req.header_items()}; seen["body"] = json.loads(req.data)
                return FakeResponse(200, CHEAP_HEADERS)
        real = kq.urllib.request.urlopen
        kq.urllib.request.urlopen = Opener()
        try: kq.probe_key("tok-123", "claude-haiku-4-5", base_url="http://x/", now=NOW)
        finally: kq.urllib.request.urlopen = real
        self.assertEqual(seen["url"], "http://x/v1/messages")
        self.assertEqual(seen["headers"]["authorization"], "Bearer tok-123"); self.assertEqual(seen["headers"]["anthropic-beta"], kq.OAUTH_BETA)
        self.assertEqual(seen["body"]["max_tokens"], 1); self.assertEqual(seen["body"]["model"], "claude-haiku-4-5"); self.assertEqual(seen["body"]["system"], kq.OAUTH_SYSTEM)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.st = kq.Store(pathlib.Path(self.dir) / "q.db"); self.addCleanup(self.st.close)

    def snap(self, headers, ts=NOW, model="m"):
        return kq.probe_key("tok", model, request=responder(200, headers), now=ts)

    def fail(self, ts=NOW, status=500, *, net=False):
        """読めなかった回のスナップ（窓は空・error だけ入る）"""
        r = responder(error=urllib.error.URLError("boom")) if net else responder(status, {}, body=b"{}")
        return kq.probe_key("tok", "m", request=r, now=ts)

    def test_full_then_cheap_keeps_oi(self):
        self.st.record("a", "1234", self.snap(FABLE_HEADERS, NOW))
        r = self.st.row("a"); self.assertEqual(r["w7d_oi_util"], 0.54); self.assertEqual(r["w7d_oi_ts"], NOW)
        cheap = {**CHEAP_HEADERS, "anthropic-ratelimit-unified-5h-utilization": "0.31"}
        self.st.record("a", "1234", self.snap(cheap, NOW + 300))
        r = self.st.row("a")
        self.assertEqual(r["w5h_util"], 0.31, "安い回の 5h は更新される")
        self.assertEqual(r["w7d_oi_util"], 0.54, "安い回は 7d_oi を消さない"); self.assertEqual(r["w7d_oi_ts"], NOW)
        self.assertEqual(r["probed_ts"], NOW + 300); self.assertEqual(r["ok_ts"], NOW + 300)

    def test_cheap_only_never_has_oi(self):
        self.st.record("a", "1234", self.snap(CHEAP_HEADERS))
        self.assertIsNone(self.st.row("a")["w7d_oi_util"])

    def test_error_keeps_previous_windows_and_records_reason(self):
        self.st.record("a", "1234", self.snap(FABLE_HEADERS, NOW))
        bad = kq.probe_key("tok", "m", request=responder(500, {}, body=b"{}"), now=NOW + 60)
        self.st.record("a", "1234", bad)
        r = self.st.row("a")
        self.assertEqual(r["error"], "HTTP 500"); self.assertEqual(r["probed_ts"], NOW + 60); self.assertEqual(r["ok_ts"], NOW)
        self.assertEqual(r["w5h_util"], 0.23, "失敗した回は窓の値を触らない")
        self.assertEqual(len(self.st.history(since=0)), 6, "失敗した回は履歴に行を足さない（実測 3 + 始点 3）")

    def test_stale_follows_last_success_not_last_attempt(self):
        """成功 → 連続失敗: probed_ts は毎回新しくなるが、古いのは残量の値なので stale は ok_ts 基準（#616）"""
        self.st.record("a", "1234", self.snap(FABLE_HEADERS, NOW))
        for bad in (self.fail(NOW + 300, 500), self.fail(NOW + 600, 401), self.fail(NOW + 900, net=True), self.fail(NOW + 1200, 500)):
            self.st.record("a", "1234", bad)
        r = self.st.row("a")
        self.assertEqual(r["probed_ts"], NOW + 1200, "失敗した回でも試行時刻は進む")
        self.assertEqual(r["ok_ts"], NOW, "最後に読めたのは最初の 1 回だけ")
        self.assertEqual(r["w5h_util"], 0.23, "表示される値は成功時のまま（据え置き）")
        s = kq.summarize(r, now=NOW + 1200)
        self.assertTrue(s["stale"], "成功から 1200 s（> STALE_S）なので古い")
        self.assertEqual(s["ok"], kq.iso(NOW)); self.assertEqual(s["probed"], kq.iso(NOW + 1200))
        # 失敗が始まったばかり（成功から 300 s）は古くない
        self.assertFalse(kq.summarize(r, now=NOW + 300)["stale"])

    def test_never_succeeded_key_is_stale(self):
        """1 度も読めていない鍵は、試行が何度あっても古い"""
        self.st.record("a", "1234", self.fail(NOW, 401))
        r = self.st.row("a")
        self.assertIsNone(r["ok_ts"]); self.assertEqual(r["probed_ts"], NOW)
        s = kq.summarize(r, now=NOW)
        self.assertTrue(s["stale"]); self.assertIsNone(s["ok"])
        self.assertIsNone(self.st.last_ok_ts(), "成功が 1 度も無ければ last_ok_ts は None")

    def test_last_ok_ts_is_the_newest_success(self):
        self.st.record("a", "1234", self.snap(FABLE_HEADERS, NOW))
        self.st.record("b", "5678", self.snap(CHEAP_HEADERS, NOW + 300))
        self.st.record("b", "5678", self.fail(NOW + 9000, 500))
        self.assertEqual(self.st.last_probe_ts(), NOW + 9000, "試行はいちばん新しい失敗")
        self.assertEqual(self.st.last_ok_ts(), NOW + 300, "成功はいちばん新しい ok_ts")

    def test_oi_window_age_is_visible_via_fable_probed(self):
        """窓ごとの鮮度: 安い回だけ成功が続くと 7d_oi は据え置きのまま古くなる。
        鍵の stale は 5h / 7d が新鮮なので False で、7d_oi が古いことは fable_probed と ok の差でしか分からない
        （窓ごとの stale フラグは今の構造には無い。#616 では構造を変えない）"""
        self.st.record("a", "1234", self.snap(FABLE_HEADERS, NOW))
        for t in (NOW + 300, NOW + 600, NOW + 900, NOW + 1200):
            self.st.record("a", "1234", self.snap(CHEAP_HEADERS, t))
        s = kq.summarize(self.st.row("a"), now=NOW + 1200)
        self.assertFalse(s["stale"], "5h / 7d は毎回読めているので鍵としては新鮮")
        self.assertEqual(s["fable_probed"], kq.iso(NOW), "7d_oi は Fable の回でしか更新されない")
        self.assertEqual(s["ok"], kq.iso(NOW + 1200))
        self.assertIn("7d_oi", [w["key"] for w in s["windows"]], "据え置きの 7d_oi は出続ける（値は古い）")

    def test_history_rows_and_window_start(self):
        self.st.record("a", "1234", self.snap(FABLE_HEADERS, NOW))
        h = self.st.history(since=0)
        real = [x for x in h if x["status"] != kq.WINDOW_START]; starts = [x for x in h if x["status"] == kq.WINDOW_START]
        self.assertEqual(sorted(x["window_key"] for x in real), ["5h", "7d", "7d_oi"])
        self.assertEqual(sorted(x["window_key"] for x in starts), ["5h", "7d", "7d_oi"])
        s5 = next(x for x in starts if x["window_key"] == "5h")
        self.assertEqual(s5["probed_ts"], RESET_5H - 5 * 3600); self.assertEqual(s5["utilization"], 0.0); self.assertEqual(s5["reset_ts"], RESET_5H)
        s7 = next(x for x in starts if x["window_key"] == "7d"); self.assertEqual(s7["probed_ts"], RESET_7D - 7 * 86400)
        # 同じ reset の 2 回目は始点を増やさない。reset が変われば（窓が更新されれば）新しい始点が 1 つ増える
        self.st.record("a", "1234", self.snap(FABLE_HEADERS, NOW + 300))
        self.assertEqual(len([x for x in self.st.history(since=0) if x["status"] == kq.WINDOW_START]), 3)
        nxt = {**CHEAP_HEADERS, "anthropic-ratelimit-unified-5h-reset": str(RESET_5H + 18000)}
        self.st.record("a", "1234", self.snap(nxt, NOW + 600))
        self.assertEqual(len([x for x in self.st.history(since=0) if x["status"] == kq.WINDOW_START and x["window_key"] == "5h"]), 2)
        # reset が無い窓は始点を置かない
        noreset = {k: v for k, v in CHEAP_HEADERS.items() if not k.endswith("-reset")}
        self.st.record("b", "9999", self.snap(noreset, NOW))
        self.assertEqual([x for x in self.st.history(since=0, name="b") if x["status"] == kq.WINDOW_START], [])

    def test_history_order_and_prune(self):
        self.st.record("a", "1234", self.snap(CHEAP_HEADERS, NOW - 40 * 86400))
        self.st.record("a", "1234", self.snap(CHEAP_HEADERS, NOW))
        ts = [x["probed_ts"] for x in self.st.history(since=0)]
        self.assertEqual(ts, sorted(ts))
        n = self.st.prune(keep_days=30)
        self.assertGreaterEqual(n, 2)
        self.assertTrue(all(x["probed_ts"] >= kq.now_ts() - 31 * 86400 for x in self.st.history(since=0)))

    def test_forget_and_keep_only(self):
        self.st.record("a", "1234", self.snap(CHEAP_HEADERS)); self.st.record("b", "5678", self.snap(CHEAP_HEADERS))
        self.st.keep_only(["b"])
        self.assertIsNone(self.st.row("a")); self.assertIsNotNone(self.st.row("b")); self.assertEqual(self.st.history(since=0, name="a"), [])
        self.st.forget("b"); self.assertEqual(self.st.current(), {})

    def test_db_is_private(self):
        self.assertEqual(os.stat(self.st.path).st_mode & 0o777, 0o600)


class RunProbeTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.keys = self.dir / "keys.json"; self.db = self.dir / "keys-quota.db"
        self.env = {"SANDBOX_KEYS": str(self.keys), "AIFACTORY_KEYS_PROBE_MODEL": "cheap-m", "AIFACTORY_KEYS_PROBE_FULL_MODEL": "fable-m",
                    "AIFACTORY_KEYS_PROBE_FULL_INTERVAL_S": "900"}
        self.calls = []

    def write(self, keys): self.keys.write_text(json.dumps({"keys": keys}), encoding="utf-8")

    def probe(self, token, model, *, base_url, timeout, now):
        self.calls.append((token, model))
        return REAL_PROBE(token, model, request=responder(200, FABLE_HEADERS if model == "fable-m" else CHEAP_HEADERS), now=now)

    def failing_probe(self, token, model, *, base_url, timeout, now):
        """読めない回（HTTP 500）。probed_ts だけが進む"""
        self.calls.append((token, model))
        return REAL_PROBE(token, model, request=responder(500, {}, body=b"{}"), now=now)

    def run_(self, mode="auto", now=NOW):
        return kq.run_probe(mode=mode, env=self.env, probe=self.probe, now=now)

    def test_paths_follow_keys_json(self):
        self.assertEqual(kq.quota_db_path(pathlib.Path("/x/keys.json")), pathlib.Path("/x/keys-quota.db"))
        self.assertEqual(kq.quota_db_path(pathlib.Path("/x/demo.keys.json")), pathlib.Path("/x/demo.keys-quota.db"))
        self.assertEqual(kq.keys_path({"SANDBOX_STATE": "/s/demo.state.json"}), pathlib.Path("/s/demo.keys.json"))
        self.assertEqual(kq.quota_db_path(env={"SANDBOX_KEYS_QUOTA": "/q.db", "SANDBOX_KEYS": "/k"}), pathlib.Path("/q.db"))

    def test_first_round_is_full_for_fable_keys_then_cheap_until_interval(self):
        self.write([{"name": "f", "token": "tf", "enabled": True, "allow": {"fable": True, "other": True}},
                    {"name": "o", "token": "to", "enabled": True, "allow": {"fable": False, "other": True}},
                    {"name": "off", "token": "tx", "enabled": False, "allow": {"fable": True, "other": True}},
                    {"name": "empty", "token": "", "enabled": True, "allow": {"fable": True, "other": True}}])
        r = self.run_()
        self.assertEqual([(c["name"], c["model"], c["full"]) for c in r["probed"]], [("f", "fable-m", True), ("o", "cheap-m", False)])
        self.assertEqual([(s["name"], s["why"]) for s in r["skipped"]], [("off", "disabled"), ("empty", "no-token")])
        self.assertEqual(r["db_file"], str(self.db)); self.assertTrue(self.db.exists())
        self.assertNotIn("tf", json.dumps(r)); self.assertNotIn("to", [c["name"] for c in r["probed"]])
        r = self.run_(now=NOW + 300)
        self.assertEqual([(c["name"], c["model"]) for c in r["probed"]], [("f", "cheap-m"), ("o", "cheap-m")], "間隔の内は安いモデル")
        r = self.run_(now=NOW + 900)
        self.assertEqual([(c["name"], c["model"]) for c in r["probed"]], [("f", "fable-m"), ("o", "cheap-m")], "間隔が空いたら Fable")
        r = self.run_(mode="cheap", now=NOW + 1800); self.assertTrue(all(c["model"] == "cheap-m" for c in r["probed"]))
        r = self.run_(mode="full", now=NOW + 1900)
        self.assertEqual([(c["name"], c["model"]) for c in r["probed"]], [("f", "fable-m"), ("o", "cheap-m")], "Fable 許可の無い鍵は full でも安いモデル")

    def test_replaced_token_and_removed_key_drop_their_history(self):
        self.write([{"name": "a", "token": "tok-1111", "enabled": True, "allow": {"fable": False, "other": True}},
                    {"name": "b", "token": "tok-2222", "enabled": True, "allow": {"fable": False, "other": True}}])
        self.run_()
        st = kq.Store(self.db); self.addCleanup(st.close)
        self.assertEqual(st.row("a")["tail4"], "1111"); self.assertGreater(len(st.history(since=0, name="a")), 0)
        self.write([{"name": "a", "token": "tok-3333", "enabled": True, "allow": {"fable": False, "other": True}}])
        self.run_(now=NOW + 300)
        self.assertEqual(st.row("a")["tail4"], "3333")
        self.assertTrue(all(x["probed_ts"] >= NOW + 300 - 7 * 86400 - 1 and x["probed_ts"] != NOW for x in st.history(since=0, name="a")), "入れ替え前の実測は残らない")
        self.assertIsNone(st.row("b"), "keys.json から消えた鍵は DB からも消える")

    def test_view_and_history_view(self):
        self.write([{"name": "f", "token": "tf", "enabled": True, "allow": {"fable": True, "other": True}}])
        v = kq.view(env=self.env, now=NOW); self.assertFalse(v["exists"]); self.assertEqual(v["keys"], {})
        self.run_()
        v = kq.view(env=self.env, now=NOW + 60)
        self.assertTrue(v["exists"]); self.assertFalse(v["stale"]); self.assertEqual(v["full_model"], "fable-m")
        s = v["keys"]["f"]
        self.assertEqual([w["key"] for w in s["windows"]], ["5h", "7d", "7d_oi"]); self.assertEqual(s["binding"]["key"], "7d")
        self.assertTrue(kq.view(env=self.env, now=NOW + 3600)["stale"])
        h = kq.history_view(env=self.env, hours=1, now=NOW + 60)
        self.assertEqual(len([p for p in h["points"] if p["status"] != kq.WINDOW_START]), 3)
        self.assertEqual(next(p for p in h["points"] if p["window"] == "7d_oi" and p["status"] != kq.WINDOW_START)["remaining_pct"], 46.0)

    def test_view_stale_follows_last_success(self):
        """パネル全体（view）の stale も最後に読めた時刻が基準。鍵ごと（summarize）と食い違わないこと（#616）"""
        self.write([{"name": "f", "token": "tf", "enabled": True, "allow": {"fable": True, "other": True}}])
        self.run_()                                       # 1 周だけ成功
        self.probe = self.failing_probe                   # 以後は読めない
        for t in (NOW + 300, NOW + 600, NOW + 900, NOW + 1200):
            self.run_(now=t)
        v = kq.view(env=self.env, now=NOW + 1200)
        self.assertEqual(v["last_probed"], kq.iso(NOW + 1200), "試行は続いている")
        self.assertEqual(v["last_ok"], kq.iso(NOW), "読めたのは最初の 1 回だけ")
        self.assertTrue(v["stale"], "パネル全体が古いと出る")
        self.assertTrue(v["keys"]["f"]["stale"], "鍵ごとも古いと出る（パネルと食い違わない）")
        self.assertEqual(v["keys"]["f"]["error"], "HTTP 500")
        # 失敗が始まったばかり（成功から 300 s）はどちらも古くない
        v = kq.view(env=self.env, now=NOW + 300)
        self.assertFalse(v["stale"]); self.assertFalse(v["keys"]["f"]["stale"])

    def test_cli_probe_prints_names_only(self):
        self.write([{"name": "a", "token": "tok-secret-1111", "enabled": True, "allow": {"fable": False, "other": True}}])
        env = {**os.environ, **self.env}
        real_env, real_probe = kq.os.environ, kq.probe_key
        buf = io.StringIO()
        try:
            kq.os.environ = env
            kq.probe_key = self.probe
            with contextlib.redirect_stdout(buf): rc = kq.main(["probe"])
        finally: kq.os.environ, kq.probe_key = real_env, real_probe
        self.assertEqual(rc, 0); out = buf.getvalue()
        self.assertIn("[probe] a: ok", out); self.assertNotIn("secret", out)
        buf = io.StringIO()
        try:
            kq.os.environ = env
            with contextlib.redirect_stdout(buf): kq.main(["show"])
        finally: kq.os.environ = real_env
        self.assertIn("5 時間枠", buf.getvalue()); self.assertNotIn("secret", buf.getvalue())


class SummarizeTest(unittest.TestCase):
    def row(self, **kw):
        base = {"name": "a", "probed_ts": NOW, "ok_ts": NOW, "error": None, "model": "m", "http_status": 200, "status": "allowed", "claim": "five_hour",
                "overage_status": None, "overage_reason": None, "w5h_util": 0.23, "w5h_reset_ts": float(RESET_5H), "w5h_status": "allowed",
                "w7d_util": 0.59, "w7d_reset_ts": float(RESET_7D), "w7d_status": "allowed", "w7d_oi_util": None, "w7d_oi_reset_ts": None, "w7d_oi_status": None, "w7d_oi_ts": None}
        base.update(kw); return base

    def test_remaining_and_binding(self):
        s = kq.summarize(self.row(), now=NOW)
        w5, w7 = s["windows"]
        self.assertEqual(w5["remaining_pct"], 77.0); self.assertEqual(w5["remain_s"], 10000); self.assertEqual(w5["elapsed_pct"], 44.4)
        self.assertEqual(w7["remaining_pct"], 41.0); self.assertEqual(s["binding"]["key"], "7d"); self.assertFalse(s["stale"])
        self.assertEqual(w5["start"], kq.iso(RESET_5H - 18000)); self.assertEqual(w5["reset"], kq.iso(RESET_5H))

    def test_pace_predicts_exhaustion_before_reset(self):
        # 5h 窓: 経過 8000 s で 90% 消費 → 残り 10% は約 889 s で尽きる（残り 10000 s より先）
        s = kq.summarize(self.row(w5h_util=0.9), now=NOW)
        w5 = s["windows"][0]
        self.assertTrue(w5["will_exhaust"]); self.assertEqual(w5["exhaust_in_s"], 888)
        # 序盤（経過 < 5%）は判定しない
        s = kq.summarize(self.row(w5h_util=0.9, w5h_reset_ts=float(NOW + 18000 - 100)), now=NOW)
        self.assertFalse(s["windows"][0]["will_exhaust"])

    def test_exhausted_and_window_end(self):
        s = kq.summarize(self.row(w5h_util=0.995), now=NOW)
        self.assertTrue(s["windows"][0]["exhausted"]); self.assertEqual(s["windows"][0]["remaining_pct"], 0.0); self.assertEqual(s["binding"]["key"], "5h")
        s = kq.summarize(self.row(w5h_util=0.5, w5h_status="rejected"), now=NOW)
        self.assertTrue(s["windows"][0]["exhausted"]); self.assertEqual(s["binding"]["remaining_pct"], 0.0)
        # reset を過ぎた窓（次の観測待ち）は逼迫の判定から外す
        s = kq.summarize(self.row(w5h_util=0.99, w5h_reset_ts=float(NOW - 10)), now=NOW)
        self.assertTrue(s["windows"][0]["at_window_end"]); self.assertEqual(s["binding"]["key"], "7d")

    def test_no_windows_and_error(self):
        s = kq.summarize(self.row(w5h_util=None, w7d_util=None, error="HTTP 401 authentication_error", ok_ts=None), now=NOW)
        self.assertEqual(s["windows"], []); self.assertIsNone(s["binding"]); self.assertEqual(s["error"], "HTTP 401 authentication_error")
        self.assertIsNone(kq.summarize(None))
        # stale は「最後に読めた時刻」が基準。試行が新しくても成功が古ければ古い（#616）
        self.assertTrue(kq.summarize(self.row(probed_ts=NOW, ok_ts=NOW - 1000), now=NOW)["stale"])
        self.assertFalse(kq.summarize(self.row(probed_ts=NOW - 1000, ok_ts=NOW - 100), now=NOW)["stale"])


if __name__ == "__main__":
    unittest.main()
