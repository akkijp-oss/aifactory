#!/usr/bin/env python3
"""lib/aifactory_keys_quota.py: 鍵プールの残量（利用枠）を観測して記録する（ADR-0087）。

  python3 lib/aifactory_keys_quota.py probe [--full|--cheap] [--wait SEC] [--json]   全部の有効な鍵を 1 周プローブして記録する
  python3 lib/aifactory_keys_quota.py show [--json]                     いまの残量（鍵 × 窓）を出す
  python3 lib/aifactory_keys_quota.py history [--hours N] [--json]      残量の履歴（グラフ用）を出す

何をするか
- 鍵ごとに極小の `POST /v1/messages`（max_tokens=1）を叩き、応答ヘッダの `anthropic-ratelimit-unified-*` を読む。
  `count_tokens` は無課金だがヘッダを返さないので、本物の messages で叩く（入出力あわせて十数トークン）。
- サブスクリプション（`claude setup-token` の OAuth 鍵）の利用枠は 3 つの窓に分かれる:
    5h     `unified-5h-*`      5 時間の枠（全モデル共通）
    7d     `unified-7d-*`      7 日の枠（全体）
    7d_oi  `unified-7d_oi-*`   7 日の枠（Fable 専用）。**model が Fable のときにしか返らない**
  だから 2 つの周期で叩く: 安いモデル（既定 haiku）で 5 分ごとに 5h / 7d、Fable で 15 分ごとに 7d_oi も（全窓）。
  安いモデルの回に 7d_oi を None で上書きしない（据え置く）。
- 現在値は `key_quota`、時系列は `key_quota_history` に SQLite で残す（keys.json の隣の `keys-quota.db`）。
  窓の始点は API が返さないが窓長が既知なので「始点 = reset − 窓長」で確定でき、始点は定義上 残量 100% なので
  `(鍵, 窓, reset)` ごとに 1 行だけ utilization 0 の合成行（status = window_start）を置く。履歴は既定 30 日で剪定する。
- 鍵の値は keys.json から読んで Anthropic に送るだけ。ログ・標準出力・DB・例外文には出さない（名前と末尾 4 文字まで）。
  世代（同名で値が入れ替わったか）は非可逆な指紋（sha256 の頭 16 桁）で見分け、指紋も DB の内部列に置くだけで外へは出さない（ADR-0090）。
- 意味の分かっていない `anthropic-ratelimit-unified-*` ヘッダは `key_quota_raw` に生のまま控える。表示にも判定にも使わない（ADR-0090）。
- 1 周はプロセス間のファイルロック（`<DB>.lock`）で直列化する。timer・console の「いま調べる」・手元の CLI が重なっても二重に叩かない。

呼ぶのは systemd の timer（`aifactory-keys-probe.timer`。5 分ごと）と console の「いま調べる」（ジョブ）。
console / MCP は `Store.current()` を `summarize()` で要約して「鍵」画面と `keys_list` に載せる（読むだけ）。
標準ライブラリだけで動く（console と同じ約束）。
"""
import argparse, datetime, fcntl, hashlib, json, os, pathlib, sqlite3, sys, time, urllib.error, urllib.request

ANTHROPIC_VERSION = "2023-06-01"
OAUTH_BETA = "oauth-2025-04-20"
# OAuth（サブスクリプション）の messages は Claude Code クライアントを前提とした system プロンプトを要求する
OAUTH_SYSTEM = "You are Claude Code, Anthropic's official CLI for Claude."

WINDOWS = ("5h", "7d", "7d_oi")
WINDOW_SECONDS = {"5h": 5 * 3600, "7d": 7 * 86400, "7d_oi": 7 * 86400}
WINDOW_START = "window_start"   # 合成した「窓の始点」行の status

DEFAULT_CHEAP_MODEL = "claude-haiku-4-5"     # 5h / 7d を取る（安い）
DEFAULT_FULL_MODEL = "claude-fable-5-1"      # 7d_oi はこのモデルでしか返らない（workflow の plan 工程と同じ ID）
DEFAULT_FULL_INTERVAL_S = 900                # Fable で叩く間隔（コスト節約のため安い回より長く）
DEFAULT_KEEP_DAYS = 30
DEFAULT_BASE_URL = "https://api.anthropic.com"
STALE_S = 900                                # 最後に「読めた」観測がこれより古ければ「古い」（timer が 5 分ごとなので 3 回分）

RAW_PREFIX = "anthropic-ratelimit-unified-"   # 未知ヘッダを拾う範囲。これ以外は絶対に保存しない（秘密が混ざる経路を作らない）
RAW_MAX = 20                                  # 1 応答あたりの本数の上限（外部から来る文字列を無条件に溜めない）
RAW_NAME_MAX, RAW_VALUE_MAX = 80, 200         # 名前・値の長さの上限（超えた分は切る）

SCHEMA = """
CREATE TABLE IF NOT EXISTS key_quota (
  name            TEXT PRIMARY KEY,
  tail4           TEXT,
  fp              TEXT,      -- トークンの非可逆な指紋（世代の識別。表示・API には出さない）
  probed_ts       REAL,      -- 最後にプローブを試みた時刻（成否問わず）
  model           TEXT,
  http_status     INTEGER,
  error           TEXT,      -- 最後の試みの失敗理由（成功なら NULL）
  ok_ts           REAL,      -- 最後にヘッダが読めた時刻
  status          TEXT, claim TEXT, overage_status TEXT, overage_reason TEXT,
  w5h_util        REAL, w5h_reset_ts REAL, w5h_status TEXT,
  w7d_util        REAL, w7d_reset_ts REAL, w7d_status TEXT,
  w7d_oi_util     REAL, w7d_oi_reset_ts REAL, w7d_oi_status TEXT, w7d_oi_ts REAL,
  updated_ts      REAL
);
CREATE TABLE IF NOT EXISTS key_quota_history (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  window_key   TEXT NOT NULL,
  utilization  REAL NOT NULL,
  reset_ts     REAL,
  status       TEXT,
  probed_ts    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kqh_name ON key_quota_history(name, window_key, probed_ts);
CREATE INDEX IF NOT EXISTS idx_kqh_ts ON key_quota_history(probed_ts);
CREATE TABLE IF NOT EXISTS key_quota_raw (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  header       TEXT NOT NULL,   -- anthropic-ratelimit-unified-* のうち、この版が意味を知らないもの
  value        TEXT,
  probed_ts    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kqr_name ON key_quota_raw(name, header, probed_ts);
CREATE INDEX IF NOT EXISTS idx_kqr_ts ON key_quota_raw(probed_ts);
"""

# 既存 DB への移行は「列を足す」だけに限る（削除・改名・型変更はしない。#167 の DB を壊さないため）
MIGRATE_COLUMNS = [("key_quota", "fp", "TEXT")]


# ---------- 置き場
def keys_path(env=None):
    """keys.json の置き場。sandbox CLI / console / kb と同じ規則（SANDBOX_KEYS → state.json の隣。テナントは <t>.keys.json）"""
    env = os.environ if env is None else env
    v = env.get("SANDBOX_KEYS")
    if v: return pathlib.Path(v)
    st = pathlib.Path(env.get("SANDBOX_STATE") or (pathlib.Path.home() / ".config" / "sandbox" / "state.json"))
    return st.parent / (st.name[:-len("state.json")] + "keys.json" if st.name.endswith("state.json") else "keys.json")


def quota_db_path(keys=None, env=None):
    """残量 DB の置き場: keys.json の隣の keys-quota.db（テナントは <t>.keys-quota.db）。SANDBOX_KEYS_QUOTA で差し替えられる"""
    env = os.environ if env is None else env
    v = env.get("SANDBOX_KEYS_QUOTA")
    if v: return pathlib.Path(v)
    k = keys or keys_path(env)
    return k.parent / (k.name[:-len("keys.json")] + "keys-quota.db" if k.name.endswith("keys.json") else k.name + "-quota.db")


# ---------- 時刻
def now_ts(): return time.time()


def iso(ts):
    """epoch 秒 → オフセット付き ISO 8601（記録の時刻の形。ADR-0026）。None はそのまま"""
    if ts is None: return None
    return datetime.datetime.fromtimestamp(float(ts)).astimezone().replace(microsecond=0).isoformat()


def _f(v):
    try: return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError): return None


def _epoch(v):
    """unified-*-reset は unix epoch 秒。読めなければ None"""
    try: return float(int(v)) if v not in (None, "") else None
    except (TypeError, ValueError): return None


# ---------- プローブ（1 本）
def _request(token, model, base_url, timeout):
    body = json.dumps({"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "x"}], "system": OAUTH_SYSTEM}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/v1/messages", data=body, method="POST", headers={
        "authorization": "Bearer " + token, "anthropic-beta": OAUTH_BETA, "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json", "accept": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _error_reason(code, raw):
    """4xx / 5xx の本文から短い理由（error.type）を取る。本文そのものは記録しない（長い・鍵の情報を含みうる）"""
    try:
        d = json.loads(raw.decode("utf-8", "replace"))
        t = ((d.get("error") or {}).get("type") if isinstance(d, dict) else None) or ""
        return f"HTTP {code} {t}".strip()
    except Exception: return f"HTTP {code}"


KNOWN_HEADERS = frozenset(
    [RAW_PREFIX + x for x in ("status", "representative-claim", "overage-status", "overage-disabled-reason")]
    + [f"{RAW_PREFIX}{w}-{f}" for w in WINDOWS for f in ("utilization", "reset", "status")])


def unknown_headers(h):
    """`anthropic-ratelimit-unified-` で始まるのにこの版が読み方を知らないヘッダ → {名前: 値}。

    #559 が実測で見つけた `unified-reset`（窓別ではない単一値）と `unified-fallback-percentage` のように、
    意味が分かっていない値でも生のまま残しておく（捨てると後で重要と分かったとき履歴が無い）。
    表示にも判定にも使わない（ADR-0090）。接頭辞の外は保存しない = 認証情報が紛れ込む経路を作らない"""
    try: items = list(h.items())
    except Exception: return {}
    raw = {}
    for k, v in items:
        k = str(k).lower()
        if not k.startswith(RAW_PREFIX) or k in KNOWN_HEADERS: continue
        raw[k[:RAW_NAME_MAX]] = str(v)[:RAW_VALUE_MAX]
        if len(raw) >= RAW_MAX: break
    return raw


def parse_headers(h):
    """応答ヘッダ → 窓ごとの観測。無い窓は入れない。h は .get(name) で引ける物（email.message / dict）"""
    g = lambda k: h.get(k)   # noqa: E731
    windows = {}
    for w in WINDOWS:
        util = _f(g(f"anthropic-ratelimit-unified-{w}-utilization"))
        if util is None: continue
        windows[w] = {"utilization": max(0.0, min(1.0, util)), "reset_ts": _epoch(g(f"anthropic-ratelimit-unified-{w}-reset")),
                      "status": g(f"anthropic-ratelimit-unified-{w}-status")}
    return {"windows": windows, "status": g("anthropic-ratelimit-unified-status"), "claim": g("anthropic-ratelimit-unified-representative-claim"),
            "overage_status": g("anthropic-ratelimit-unified-overage-status"), "overage_reason": g("anthropic-ratelimit-unified-overage-disabled-reason"),
            "raw": unknown_headers(h)}


def probe_key(token, model, *, base_url=DEFAULT_BASE_URL, timeout=20, now=None, request=_request):
    """鍵 1 本を叩いてスナップショットを返す。失敗しても例外にせず error に理由を入れる（鍵の値は含めない）。

    429 はレート制限到達 = ヘッダに残量が載っているので成功として読む。それ以外の 4xx / 5xx は残量が取れない。
    covers_all は 7d_oi が読めたか（Fable で叩いたときだけ True になる）"""
    ts = now if now is not None else now_ts()
    snap = {"probed_ts": ts, "model": model, "http_status": None, "error": None, "windows": {}, "covers_all": False,
            "status": None, "claim": None, "overage_status": None, "overage_reason": None, "raw": {}}
    try:
        with request(token, model, base_url, timeout) as r:
            snap["http_status"] = r.status
            snap.update(parse_headers(r.headers))
    except urllib.error.HTTPError as e:
        snap["http_status"] = e.code
        if e.code == 429:
            snap.update(parse_headers(e.headers))
        else:
            snap["error"] = _error_reason(e.code, e.read() if hasattr(e, "read") else b"")
    except urllib.error.URLError as e:
        snap["error"] = "network: %s" % (getattr(e, "reason", e),)
    except Exception as e:   # timeout 等。プローブは観測専用なので何があっても止めない
        snap["error"] = "%s: %s" % (type(e).__name__, e)
    if snap["error"] is None and not snap["windows"]:
        snap["error"] = "no-ratelimit-headers"   # 200 なのにヘッダが無い（API 鍵や別の経路）。残量は読めない
    snap["covers_all"] = "7d_oi" in snap["windows"]
    return snap


# ---------- 保存
class Store:
    """keys-quota.db。書くのは probe だけ、console / MCP は読むだけ（read_only=True で開く）"""

    def __init__(self, path, *, read_only=False):
        self.path = pathlib.Path(path)
        if read_only:
            self.c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.c = sqlite3.connect(str(self.path), check_same_thread=False)
            self.c.execute("PRAGMA journal_mode=WAL")
            self.c.executescript(SCHEMA)
            self._migrate()
            try: os.chmod(self.path, 0o600)
            except OSError: pass
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA busy_timeout=5000")

    def _migrate(self):
        """先に作られた DB に、後から足した列を入れる（追加のみ）。読むだけの側（console）は移行しない。
        古い DB を console が読んでも新しい列が無いだけで壊れない"""
        with self.c:
            for table, col, decl in MIGRATE_COLUMNS:
                have = {r[1] for r in self.c.execute(f"PRAGMA table_info({table})").fetchall()}   # row_factory 前なので添字で引く
                if col not in have: self.c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    def close(self): self.c.close()

    def row(self, name):
        r = self.c.execute("SELECT * FROM key_quota WHERE name=?", (name,)).fetchone()
        return dict(r) if r else None

    def forget(self, name):
        """鍵が消えた / 値が入れ替わった: その名前の現在値と履歴を消す（別の契約の残量を引き継がない）"""
        with self.c:
            for t in ("key_quota", "key_quota_history", "key_quota_raw"):
                self.c.execute(f"DELETE FROM {t} WHERE name=?", (name,))

    def keep_only(self, names):
        names = list(names)
        with self.c:
            q = ",".join("?" * len(names)) or "''"
            for t in ("key_quota", "key_quota_history", "key_quota_raw"):
                self.c.execute(f"DELETE FROM {t} WHERE name NOT IN ({q})", names)

    def record(self, name, tail4, snap, fp=None):
        """スナップショットを現在値に書き、観測できた窓を履歴に足す。7d_oi は covers_all のときだけ触る（安い回で消さない）。

        fp はトークンの非可逆な指紋（`fingerprint()`）。省略すると前の値を据え置く（fp を知らない呼び手のため）。
        巻き戻り防止: 最後に読めた時刻より前の観測（順序が逆転した応答）で現在値を上書きしない。履歴と未知ヘッダには残す"""
        w = snap["windows"]; ts = snap["probed_ts"]; ok = snap["error"] is None
        with self.c:
            self.c.execute("INSERT OR IGNORE INTO key_quota(name) VALUES (?)", (name,))
            prev_ok = self.c.execute("SELECT ok_ts FROM key_quota WHERE name=?", (name,)).fetchone()[0]
            behind = prev_ok is not None and ts < float(prev_ok)
            if not behind:
                self.c.execute("UPDATE key_quota SET tail4=?, fp=COALESCE(?, fp), probed_ts=?, model=?, http_status=?, error=?, updated_ts=? WHERE name=?",
                               (tail4, fp, ts, snap["model"], snap["http_status"], snap["error"], now_ts(), name))
            if not ok: return
            if not behind:
                self.c.execute("""UPDATE key_quota SET ok_ts=?, status=?, claim=?, overage_status=?, overage_reason=?,
                                    w5h_util=?, w5h_reset_ts=?, w5h_status=?, w7d_util=?, w7d_reset_ts=?, w7d_status=? WHERE name=?""",
                               (ts, snap["status"], snap["claim"], snap["overage_status"], snap["overage_reason"],
                                *self._w(w, "5h"), *self._w(w, "7d"), name))
                if snap["covers_all"]:
                    self.c.execute("UPDATE key_quota SET w7d_oi_util=?, w7d_oi_reset_ts=?, w7d_oi_status=?, w7d_oi_ts=? WHERE name=?",
                                   (*self._w(w, "7d_oi"), ts, name))
            for k, v in w.items():
                self.c.execute("INSERT INTO key_quota_history(name, window_key, utilization, reset_ts, status, probed_ts) VALUES (?,?,?,?,?,?)",
                               (name, k, v["utilization"], v["reset_ts"], v["status"], ts))
                self._window_start(name, k, v["reset_ts"])
            for h, v in (snap.get("raw") or {}).items():
                self.c.execute("INSERT INTO key_quota_raw(name, header, value, probed_ts) VALUES (?,?,?,?)", (name, h, v, ts))

    @staticmethod
    def _w(w, k):
        v = w.get(k) or {}
        return (v.get("utilization"), v.get("reset_ts"), v.get("status"))

    def _window_start(self, name, k, reset_ts):
        """窓の始点（= 残量 100%）を (鍵, 窓, reset) につき 1 行だけ合成する。観測開始前に始まった窓も遡って置く"""
        if reset_ts is None: return
        length = WINDOW_SECONDS[k]
        if self.c.execute("SELECT 1 FROM key_quota_history WHERE name=? AND window_key=? AND reset_ts=? AND status=? LIMIT 1",
                          (name, k, reset_ts, WINDOW_START)).fetchone(): return
        self.c.execute("INSERT INTO key_quota_history(name, window_key, utilization, reset_ts, status, probed_ts) VALUES (?,?,0.0,?,?,?)",
                       (name, k, reset_ts, WINDOW_START, reset_ts - length))

    def prune(self, keep_days=DEFAULT_KEEP_DAYS):
        lo = now_ts() - keep_days * 86400
        with self.c:
            n = self.c.execute("DELETE FROM key_quota_history WHERE probed_ts < ?", (lo,)).rowcount or 0
            n += self.c.execute("DELETE FROM key_quota_raw WHERE probed_ts < ?", (lo,)).rowcount or 0
            return n

    def raw(self, name=None, since=0):
        """未知ヘッダの控え。表示にも判定にも使わない（診断のときだけ sqlite3 か これで読む。ADR-0090）"""
        sql, p = "SELECT name, header, value, probed_ts FROM key_quota_raw WHERE probed_ts > ?", [since]
        if name: sql += " AND name=?"; p.append(name)
        return [dict(r) for r in self.c.execute(sql + " ORDER BY probed_ts, id", p).fetchall()]

    def current(self):
        return {r["name"]: dict(r) for r in self.c.execute("SELECT * FROM key_quota ORDER BY name").fetchall()}

    def last_probe_ts(self):
        r = self.c.execute("SELECT MAX(probed_ts) AS t FROM key_quota").fetchone()
        return r["t"] if r else None

    def last_ok_ts(self):
        """最後にヘッダが読めた時刻（試行ではなく成功）。鮮度の判定はこちらを使う（#616）"""
        r = self.c.execute("SELECT MAX(ok_ts) AS t FROM key_quota").fetchone()
        return r["t"] if r else None

    def history(self, hours=24, name=None, since=None):
        """probed_ts 昇順（同秒は id 順）。since を渡せば hours より優先"""
        lo = since if since is not None else now_ts() - hours * 3600
        sql, p = "SELECT name, window_key, utilization, reset_ts, status, probed_ts FROM key_quota_history WHERE probed_ts > ?", [lo]
        if name: sql += " AND name=?"; p.append(name)
        return [dict(r) for r in self.c.execute(sql + " ORDER BY probed_ts, id", p).fetchall()]


# ---------- 1 周
def load_keys(path):
    """keys.json を読む。値はここから probe に渡すだけ（返す辞書は呼び手の外に出さない）"""
    try: raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8")).get("keys") or []
    except FileNotFoundError: return []
    return [k for k in raw if isinstance(k, dict) and k.get("name")]


def fingerprint(token):
    """トークンの非可逆な指紋。世代（同名で値が入れ替わった / 同じ末尾 4 文字の別トークン）の識別だけに使う。

    値そのものは保存しない。指紋は DB の内部列に置くだけで、view / API / MCP / CLI には出さない（ADR-0090）"""
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:16] if token else ""


def probe_lock_path(db_path):
    """プローブ 1 周の排他に使うファイル。DB 本体ではなく隣に取る（SQLite の内部ロックと混ぜない）"""
    return pathlib.Path(str(db_path) + ".lock")


def acquire_probe_lock(path, wait_s=0.0):
    """1 周の排他を取る。取れたら fd、取れなければ None（wait_s まで 0.5 秒ごとに待つ）。

    timer（systemd）・console の「いま調べる」・手元の CLI は別プロセスなので、JobStore だけでは重なりを防げない。
    重なると同じ鍵に二重に課金プローブを打つので、プロセス間のファイルロックで 1 周ごと直列化する"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + max(0.0, float(wait_s))
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd); return None
            time.sleep(0.5)


def release_probe_lock(fd):
    if fd is None: return
    try: fcntl.flock(fd, fcntl.LOCK_UN)
    finally: os.close(fd)


def settings(env=None):
    env = os.environ if env is None else env
    g = lambda k, d: env.get(k) or d   # noqa: E731
    return {"cheap_model": g("AIFACTORY_KEYS_PROBE_MODEL", DEFAULT_CHEAP_MODEL), "full_model": g("AIFACTORY_KEYS_PROBE_FULL_MODEL", DEFAULT_FULL_MODEL),
            "full_interval_s": int(g("AIFACTORY_KEYS_PROBE_FULL_INTERVAL_S", DEFAULT_FULL_INTERVAL_S)),
            "keep_days": int(g("AIFACTORY_KEYS_QUOTA_KEEP_DAYS", DEFAULT_KEEP_DAYS)), "base_url": g("AIFACTORY_ANTHROPIC_BASE_URL", DEFAULT_BASE_URL),
            "timeout": float(g("AIFACTORY_KEYS_PROBE_TIMEOUT_S", 20))}


def _still_the_same_key(keys_file, name, fp):
    """プローブの通信中に鍵が入れ替わった / 消えた / 無効にされたかを見る。理由（変わっていなければ None）を返す。

    通信は数秒かかる。その間に console から鍵を差し替えられると、戻ってきた応答は**前の鍵**の残量なので、
    新しい鍵の残量として保存してはいけない（その回は捨てる。次の周で forget が効いて履歴も切り替わる）"""
    cur = next((k for k in load_keys(keys_file) if str(k["name"]) == name), None)
    if cur is None: return "removed-during-probe"
    if cur.get("enabled") is False: return "disabled-during-probe"
    if fingerprint(str(cur.get("token") or "")) != fp: return "replaced-during-probe"
    return None


def run_probe(keys_file=None, db_file=None, *, mode="auto", env=None, probe=None, now=None, wait_lock_s=0.0):
    """有効な鍵を全部 1 周プローブして記録する。戻り値は名前と成否だけ（値は含まない）。

    mode: auto = Fable 許可の鍵は前回の全窓観測から full_interval_s 以上経っていれば Fable で（7d_oi も取る）、それ以外は安いモデルで。
          full = Fable 許可の鍵は必ず Fable で。cheap = 全部安いモデルで。
    Fable 許可の無い鍵は常に安いモデル（その契約に Fable の枠は無い）

    1 周はプロセス間のファイルロックで直列化する。取れなければ 1 本も叩かずに locked: True で返る（失敗ではない）。
    wait_lock_s を渡すとその秒数まで待つ（手動の「いま調べる」は timer の後ろに並びたいので待つ）"""
    env = os.environ if env is None else env
    probe = probe or probe_key   # 呼ぶ時点の関数（テストが差し替えられるように既定を束縛しない）
    s = settings(env)
    kp = pathlib.Path(keys_file) if keys_file else keys_path(env)
    dp = pathlib.Path(db_file) if db_file else quota_db_path(kp, env)
    ts = now if now is not None else now_ts()
    out = {"keys_file": str(kp), "db_file": str(dp), "locked": False, "probed": [], "skipped": [], "pruned": 0}
    lock = acquire_probe_lock(probe_lock_path(dp), wait_lock_s)
    if lock is None:
        out["locked"] = True
        return out
    st = Store(dp)
    keys = load_keys(kp)
    try:
        st.keep_only([k["name"] for k in keys])
        for k in keys:
            name = str(k["name"]); tok = str(k.get("token") or ""); tail4 = tok[-4:]
            if k.get("enabled") is False or not tok:
                out["skipped"].append({"name": name, "why": "disabled" if k.get("enabled") is False else "no-token"}); continue
            fp = fingerprint(tok)
            prev = st.row(name)
            # 値が入れ替わった → 別の契約かもしれないので前の履歴を引き継がない。
            # 指紋があれば指紋で見る（末尾 4 文字が同じ別トークンも検知できる）。指紋を持たない古い行は今までどおり末尾 4 文字で
            prev_fp = (prev or {}).get("fp")
            if prev and (prev_fp != fp if prev_fp else (prev.get("tail4") and prev["tail4"] != tail4)):
                st.forget(name); prev = None
            allow = k.get("allow") if isinstance(k.get("allow"), dict) else {}
            fable_ok = allow.get("fable") is True
            if not fable_ok or mode == "cheap": full = False
            elif mode == "full": full = True
            else:
                last_full = (prev or {}).get("w7d_oi_ts")
                full = last_full is None or ts - float(last_full) >= s["full_interval_s"]
            model = s["full_model"] if full else s["cheap_model"]
            snap = probe(tok, model, base_url=s["base_url"], timeout=s["timeout"], now=ts)
            changed = _still_the_same_key(kp, name, fp)
            if changed:   # 叩いている間に鍵が変わった: この応答は前の鍵のものなので保存しない
                out["skipped"].append({"name": name, "why": changed}); continue
            st.record(name, tail4, snap, fp=fp)
            out["probed"].append({"name": name, "model": model, "full": full, "ok": snap["error"] is None, "http_status": snap["http_status"],
                                  "error": snap["error"], "windows": sorted(snap["windows"])})
        out["pruned"] = st.prune(s["keep_days"])
    finally:
        st.close()
        release_probe_lock(lock)
    return out


# ---------- 要約（console / MCP / CLI が同じ数字を出すための 1 か所）
def window_summary(util, reset_ts, status, key, now):
    """窓 1 つの読み方。残量% / 始点と終点 / 残り時間 / 経過に対する消費ペース（aix の WindowProgress と同じ規則）。

    枯渇（exhausted）は API が `rejected` と言った窓だけ。使用率がいくら高くても、断られていないなら枯渇ではない。
    残量が少ないことは remaining_pct で分かるので、「あと何 % から警告か」は表示側の閾値に任せる（ADR-0090）"""
    length = WINDOW_SECONDS[key]
    exhausted = status == "rejected"
    remaining_pct = round((1.0 - util) * 100, 1)   # 実際に残っている分を出す（残り 1% を 0% に潰さない）
    d = {"key": key, "utilization": round(util, 4), "remaining_pct": remaining_pct, "status": status, "exhausted": exhausted,
         "reset": iso(reset_ts), "start": None, "remain_s": None, "elapsed_pct": None, "at_window_end": False, "exhaust_in_s": None, "will_exhaust": False}
    if reset_ts is None: return d
    start = reset_ts - length; remain = reset_ts - now; elapsed = now - start
    d["start"] = iso(start); d["remain_s"] = int(remain); d["at_window_end"] = remain <= 0
    d["elapsed_pct"] = round(max(0.0, min(1.0, elapsed / length)) * 100, 1)
    # 序盤（経過 < 窓の 5%）は分母が不安定なのでペースを出さない
    if not d["at_window_end"] and not exhausted and elapsed >= length * 0.05 and util > 0:
        exhaust_in = (1.0 - util) / (util / elapsed)
        if exhaust_in < remain: d["exhaust_in_s"] = int(exhaust_in); d["will_exhaust"] = True
    return d


def summarize(row, now=None):
    """key_quota の 1 行 → 画面 / MCP に出す形。窓が 1 つも無ければ windows は空で、probed / error だけが入る"""
    now = now if now is not None else now_ts()
    if not row: return None
    windows = []
    for k, col in (("5h", "w5h"), ("7d", "w7d"), ("7d_oi", "w7d_oi")):
        u = row.get(col + "_util")
        if u is None: continue
        windows.append(window_summary(float(u), row.get(col + "_reset_ts"), row.get(col + "_status"), k, now))
    live = [w for w in windows if not w["at_window_end"]] or windows
    # いちばん逼迫している窓: 断られている窓が最優先、次に残量の少ない順（残量だけで選ぶと、
    # 使用率が低いのに rejected な窓を見落とす）
    binding = min(live, key=lambda w: (not w["exhausted"], w["remaining_pct"])) if live else None
    probed_ts, ok_ts = row.get("probed_ts"), row.get("ok_ts")
    # stale は「最後に読めた時刻（ok_ts）」が基準。試行時刻（probed_ts）だと失敗が続く間も新鮮に見え、
    # 据え置きの古い残量を「新しい」と言ってしまう（#616）
    return {"probed": iso(probed_ts), "ok": iso(ok_ts), "stale": ok_ts is None or now - float(ok_ts) > STALE_S,
            "model": row.get("model"), "http_status": row.get("http_status"), "error": row.get("error"),
            "status": row.get("status"), "claim": row.get("claim"), "overage_status": row.get("overage_status"), "overage_reason": row.get("overage_reason"),
            "fable_probed": iso(row.get("w7d_oi_ts")), "windows": windows,
            "binding": None if binding is None else {"key": binding["key"], "remaining_pct": binding["remaining_pct"], "reset": binding["reset"],
                                                     "exhausted": binding["exhausted"]}}


def view(keys_file=None, db_file=None, *, env=None, now=None):
    """読むだけ（console / MCP 用）: 名前 → summarize。DB が無ければ exists: False。
    パネル全体の stale も鍵ごとと同じく「最後に読めた時刻」が基準（食い違わせない。#616）"""
    env = os.environ if env is None else env
    kp = pathlib.Path(keys_file) if keys_file else keys_path(env)
    dp = pathlib.Path(db_file) if db_file else quota_db_path(kp, env)
    now = now if now is not None else now_ts()
    # last_probed = 最後に試みた時刻、last_ok = 最後に読めた時刻。stale は last_ok が基準（#616）
    out = {"db_file": str(dp), "exists": dp.exists(), "error": None, "last_probed": None, "last_ok": None, "stale": True, "keys": {},
           "cheap_model": settings(env)["cheap_model"], "full_model": settings(env)["full_model"]}
    if not dp.exists(): return out
    try:
        st = Store(dp, read_only=True)
        try:
            for name, row in st.current().items(): out["keys"][name] = summarize(row, now)
            t, t_ok = st.last_probe_ts(), st.last_ok_ts()
        finally: st.close()
        out["last_probed"] = iso(t); out["last_ok"] = iso(t_ok)
        out["stale"] = t_ok is None or now - float(t_ok) > STALE_S
    except sqlite3.Error as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def history_view(keys_file=None, db_file=None, *, hours=24, name=None, env=None, now=None):
    """グラフ用の履歴。点は {name, window, remaining_pct, reset, status, at}（at = probed 時刻の ISO）"""
    env = os.environ if env is None else env
    kp = pathlib.Path(keys_file) if keys_file else keys_path(env)
    dp = pathlib.Path(db_file) if db_file else quota_db_path(kp, env)
    out = {"hours": hours, "points": [], "exists": dp.exists()}
    if not dp.exists(): return out
    st = Store(dp, read_only=True)
    try:
        for r in st.history(hours=hours, name=name, since=(now - hours * 3600) if now is not None else None):
            out["points"].append({"name": r["name"], "window": r["window_key"], "remaining_pct": round((1.0 - r["utilization"]) * 100, 1),
                                  "reset": iso(r["reset_ts"]), "status": r["status"], "at": iso(r["probed_ts"]), "ts": r["probed_ts"]})
    finally: st.close()
    return out


# ---------- CLI
WINDOW_LABEL = {"5h": "5 時間枠", "7d": "7 日枠（全体）", "7d_oi": "7 日枠（Fable）"}


def _fmt_pct(p):
    """残り 10% 未満は小数 1 桁で出す（0.5% を「0%」と見せて枯渇と読ませない）"""
    return f"{p:.1f}%" if p < 10 else f"{p:.0f}%"


def _fmt_dur(s):
    if s is None: return "-"
    s = max(0, int(s)); h, m = s // 3600, (s % 3600) // 60
    return f"{h // 24} 日 {h % 24} 時間" if h >= 24 else (f"{h} 時間 {m} 分" if h else f"{m} 分")


def main(argv=None):
    ap = argparse.ArgumentParser(description="鍵プールの残量（利用枠）を観測する。値は出さない")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="全部の有効な鍵を 1 周叩いて記録する")
    g = p.add_mutually_exclusive_group(); g.add_argument("--full", action="store_true", help="Fable 許可の鍵は必ず Fable で叩く（7d_oi も取る）")
    g.add_argument("--cheap", action="store_true", help="全部安いモデルで叩く（7d_oi は据え置き）")
    p.add_argument("--wait", type=float, default=0.0, metavar="SEC", help="別のプローブが実行中なら最大この秒数まで待つ（既定 0 = 待たずに飛ばす）")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show", help="いまの残量"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("history", help="残量の履歴"); p.add_argument("--hours", type=int, default=24); p.add_argument("--name"); p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "probe":
        r = run_probe(mode="full" if a.full else "cheap" if a.cheap else "auto", wait_lock_s=a.wait)
        if a.json: print(json.dumps(r, ensure_ascii=False)); return 0
        if r["locked"]:
            print("別のプローブが実行中（timer か console）。今回は飛ばしました（--wait 秒数 で待てます）"); return 0
        for x in r["probed"]:
            print(f"[probe] {x['name']}: {'ok' if x['ok'] else 'NG ' + str(x['error'])} model={x['model']}{' (full)' if x['full'] else ''} windows={','.join(x['windows']) or '-'}")
        for x in r["skipped"]: print(f"[probe] {x['name']}: skip ({x['why']})")
        print(f"file: {r['db_file']}（{len(r['probed'])} 本を観測、{r['pruned']} 行を剪定）")
        return 0 if all(x["ok"] for x in r["probed"]) else 1
    if a.cmd == "show":
        v = view()
        if a.json: print(json.dumps(v, ensure_ascii=False)); return 0
        if not v["exists"]: print(f"まだ観測していません（{v['db_file']}）。python3 lib/aifactory_keys_quota.py probe で 1 周叩けます"); return 0
        fmt = "%-20s %-16s %8s %-16s %-10s %s"
        print(fmt % ("NAME", "WINDOW", "REMAIN", "RESET_IN", "STATUS", "NOTE"))
        for name, s in v["keys"].items():
            if not s["windows"]:
                print(fmt % (name, "-", "-", "-", "-", s["error"] or "未観測")); continue
            for w in s["windows"]:
                note = "枯渇" if w["exhausted"] else ("リセット待ち" if w["at_window_end"] else (f"このペースだと約 {_fmt_dur(w['exhaust_in_s'])} で枯渇" if w["will_exhaust"] else ""))
                print(fmt % (name, WINDOW_LABEL[w["key"]], _fmt_pct(w["remaining_pct"]), _fmt_dur(w["remain_s"]), w["status"] or "-", note))
            if s["error"]: print(fmt % (name, "-", "-", "-", "-", f"最後の観測は {s['error']}（値は前回のもの）"))
        # 試行と成功を並べる（試行だけ新しいのに「古い」と出る理由が読めるように。#616）
        ok = f"・最後に読めたのは {v['last_ok'] or '一度もありません'}" if v["last_ok"] != v["last_probed"] else ""
        print(f"file: {v['db_file']}（最終観測 {v['last_probed'] or '-'}{ok}{'・古い' if v['stale'] else ''}）")
        return 0
    if a.cmd == "history":
        h = history_view(hours=a.hours, name=a.name)
        if a.json: print(json.dumps(h, ensure_ascii=False)); return 0
        for p_ in h["points"]: print(f"{p_['at']} {p_['name']:<20} {p_['window']:<6} {p_['remaining_pct']:5.1f}% {p_['status'] or ''}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
