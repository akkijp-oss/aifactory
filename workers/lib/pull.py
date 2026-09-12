"""Durable, single-tenant pull queue. No host commands are accepted over HTTP.

Administrative operations use the local CLI/database. The network API grants a
worker access only to its own operations. Lost workers are never reassigned.
"""
import contextlib
import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import sqlite3
import ssl
import time
import uuid
from pathlib import Path

VERSION = 1
MAX_BODY = 1024 * 1024
MAX_LOG = 16 * 1024 * 1024
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")

# 制御系 DB のロックの待ち方（チケット 446）。run が何本も同時に回ると同じ sqlite を 6 本以上の接続が叩く。
# sqlite の busy 待ちには公平性が無いので、運の悪い 1 本が待ち切れず database is locked を食う。
# 1 文を長く待たせるのではなく、トランザクションごと短くやり直す（待ち直しの間に他が commit する）。
# BUSY_TIMEOUT_MS を worker の HTTP client の待ち（10 秒。cmd/aifactory-worker/main.go）より短く、
# LOCK_RETRY_BUDGET_S をその待ちに収めること。長く抱えると worker が見切って叩き直し、競合が増える
BUSY_TIMEOUT_MS = 3000            # 1 文がロックの解放を待つ上限
LOCK_RETRY_ATTEMPTS = 6           # トランザクションを何回までやり直すか
LOCK_RETRY_BUDGET_S = 8.0         # やり直しを含めた 1 回の呼び出しの総時間の上限
LOCK_RETRY_DELAY_S = 0.05         # 最初の待ち（以降倍々）
LOCK_RETRY_MAX_DELAY_S = 1.0


def locked(exc):
    """sqlite が「他が握っているので今は無理」と言っているだけか（= やり直せば通る）"""
    return isinstance(exc, sqlite3.OperationalError) and ("locked" in str(exc) or "busy" in str(exc))


class Error(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.tx(lambda db: db.executescript("""
            CREATE TABLE IF NOT EXISTS workers (
              id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
              last_seen REAL NOT NULL DEFAULT 0, info TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS operations (
              id TEXT PRIMARY KEY, worker TEXT NOT NULL REFERENCES workers(id),
              kind TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
              created REAL NOT NULL, result TEXT, cancelled INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events (
              op TEXT NOT NULL REFERENCES operations(id), seq INTEGER NOT NULL, text TEXT NOT NULL,
              PRIMARY KEY(op, seq));
            CREATE TABLE IF NOT EXISTS leases (
              worker TEXT PRIMARY KEY REFERENCES workers(id), id TEXT NOT NULL UNIQUE,
              created REAL NOT NULL);
            DROP INDEX IF EXISTS one_active_operation;
            CREATE UNIQUE INDEX IF NOT EXISTS one_reserved_worker
              ON operations(worker) WHERE state IN ('queued', 'running', 'uncertain');
            """))
        self.path.chmod(0o600)
        # 読みと書きの排他を減らす（WAL なら Client が毎秒叩く operation() が worker の書きとぶつからない。446）。
        # rollback journal の既存 DB はここで移行する。chmod の後に打つこと: 横に出る -wal / -shm は
        # 作られた時点の主 DB の権限を継ぐので、先に打つと 0600 になる前の権限で作られる。
        # 移行できない置き場（journal_mode を変えられない）なら黙って従来のまま続ける（やり直しだけで凌ぐ）
        with contextlib.closing(sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000)) as db:
            with contextlib.suppress(sqlite3.DatabaseError):
                db.execute("PRAGMA journal_mode=WAL")

    @contextlib.contextmanager
    def db(self, busy_ms=None):
        busy = BUSY_TIMEOUT_MS if busy_ms is None else busy_ms
        db = sqlite3.connect(self.path, timeout=busy / 1000)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(f"PRAGMA busy_timeout={int(busy)}")     # connect(timeout=) と同じものを明示する
        try:
            with db:
                yield db
        finally:
            db.close()

    def tx(self, fn):
        """fn(db) を 1 つのトランザクションで回す。ロックで弾かれたら予算内で丸ごとやり直す（446）。

        commit まで行けなかった回は rollback されているので、同じ fn をもう一度回しても
        DB には何も残っていない。業務エラー（Error）や壊れた DB はそのまま外へ出す。
        fn は DB 以外の副作用を持ってよいが、何度回しても同じ結果になること（submit の spool 参照）
        """
        deadline = time.monotonic() + LOCK_RETRY_BUDGET_S
        delay = LOCK_RETRY_DELAY_S
        for attempt in range(1, LOCK_RETRY_ATTEMPTS + 1):
            left = deadline - time.monotonic()
            try:
                # 1 文の待ちも残り予算で頭を押さえる。押さえないと総時間が予算 + busy_timeout になる
                with self.db(max(1, min(BUSY_TIMEOUT_MS, int(left * 1000)))) as db:
                    return fn(db)
            except sqlite3.OperationalError as e:
                # ロック以外（DB が壊れた等）と、予算・回数を使い切った回はそのまま外へ
                if not locked(e) or attempt == LOCK_RETRY_ATTEMPTS or time.monotonic() + delay >= deadline:
                    raise
            time.sleep(delay)
            delay = min(delay * 2, LOCK_RETRY_MAX_DELAY_S)

    def enroll(self, worker):
        if not NAME.fullmatch(worker):
            raise Error("invalid worker name")
        token = secrets.token_urlsafe(32)
        try:
            self.tx(lambda db: db.execute("INSERT INTO workers(id,token_hash) VALUES (?,?)", (worker, digest(token))))
        except sqlite3.IntegrityError:
            raise Error("worker already registered", 409)
        return token

    def authenticate(self, worker, token):
        row = self.tx(lambda db: db.execute("SELECT * FROM workers WHERE id=?", (worker,)).fetchone())
        if not row or not row["enabled"] or not hmac.compare_digest(row["token_hash"], digest(token)):
            raise Error("unauthorized", 401)

    def disable(self, worker):
        self.tx(lambda db: db.execute("UPDATE workers SET enabled=0 WHERE id=?", (worker,)))

    def workers(self):
        def work(db):
            rows = db.execute("SELECT id,enabled,last_seen,info FROM workers ORDER BY id").fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["info"] = json.loads(d["info"])
                d["online"] = bool(d["enabled"] and time.time() - d["last_seen"] < 45)
                active = db.execute("SELECT id,state FROM operations WHERE worker=? AND state IN ('queued','running','uncertain')", (d["id"],)).fetchone()
                d["operation"] = dict(active) if active else None
                lease = db.execute("SELECT id,created FROM leases WHERE worker=?", (d["id"],)).fetchone()
                d["lease"] = dict(lease) if lease else None
                result.append(d)
            return result
        return self.tx(work)

    def acquire(self, worker, lease):
        if not NAME.fullmatch(lease):
            raise Error("invalid lease ID")

        def work(db):
            db.execute("BEGIN IMMEDIATE")
            w = db.execute("SELECT * FROM workers WHERE id=? AND enabled=1", (worker,)).fetchone()
            if not w or time.time() - w["last_seen"] > 45:
                raise Error("worker unavailable", 409)
            if not json.loads(w["info"]).get("lifecycle"):
                raise Error("worker has no lifecycle support", 409)
            if json.loads(w["info"]).get("base_ready") is False:
                raise Error("worker base image not ready", 409)
            if json.loads(w["info"]).get("network_ready") is False:
                raise Error("worker network setup not ready", 409)
            old = db.execute("SELECT id FROM leases WHERE worker=?", (worker,)).fetchone()
            if old:
                if old[0] == lease: return
                raise Error("worker leased to another run", 409)
            if db.execute("SELECT 1 FROM operations WHERE worker=? AND state IN ('queued','running','uncertain')", (worker,)).fetchone():
                raise Error("worker busy", 409)
            db.execute("INSERT INTO leases VALUES (?,?,?)", (worker, lease, time.time()))
        return self.tx(work)

    def release_lease(self, worker, lease, operation):
        def work(db):
            db.execute("BEGIN IMMEDIATE")      # 読んでから書くので、他の書き手より先に取る（後から昇格すると即 BUSY）
            op = self.owned(db, worker, operation)
            if op["kind"] != "guest-release" or op["state"] != "succeeded" or json.loads(op["payload"]).get("lease") != lease:
                raise Error("successful guest release required", 409)
            db.execute("DELETE FROM leases WHERE worker=? AND id=?", (worker, lease))
        return self.tx(work)

    def input_path(self, operation):
        if not NAME.fullmatch(operation): raise Error("invalid operation ID")
        return self.path.parent / (self.path.name + ".inputs") / operation

    def submit(self, worker, kind, payload, operation=None, stdin=None):
        operation = operation or str(uuid.uuid4())
        if not NAME.fullmatch(operation):
            raise Error("invalid operation ID")
        if kind not in ("probe", "guest-exec", "guest-prepare", "guest-release"):
            raise Error("unsupported operation")
        if not isinstance(payload, dict):
            raise Error("payload must be an object")
        if kind == "probe" and payload:
            raise Error("probe accepts no arguments")
        if kind == "guest-exec":
            if set(payload) - {"command", "timeout", "lease"} or not isinstance(payload.get("command"), str):
                raise Error("guest-exec requires command and optional timeout")
            payload = {"timeout": 300, **payload}
            if type(payload["timeout"]) is not int or not 1 <= payload["timeout"] <= 3600:
                raise Error("timeout must be between 1 and 3600 seconds")
        if kind in ("guest-prepare", "guest-release"):
            allowed = {"lease", "width", "height"} if kind == "guest-prepare" else {"lease"}
            if set(payload) - allowed or "lease" not in payload or not NAME.fullmatch(str(payload["lease"])):
                raise Error("lifecycle requires a lease ID")
            # \u89e3\u50cf\u5ea6\u306f guest-prepare \u306e\u3068\u304d\u3060\u3051\u3001width/height \u63c3\u3044\u3067 project.schema.json \u3068\u540c\u3058\u5024\u57df\u306e\u3068\u304d\u3060\u3051\u901a\u3059\uff08343\uff09
            size = {"width", "height"} & set(payload)
            if size and size != {"width", "height"}:
                raise Error("display requires both width and height")
            if size and (type(payload["width"]) is not int or not 800 <= payload["width"] <= 2560
                         or type(payload["height"]) is not int or not 600 <= payload["height"] <= 2560):
                raise Error("display width must be 800-2560 and height 600-2560")
        if stdin is not None:
            if kind != "guest-exec" or not isinstance(stdin, str) or len(stdin.encode()) > 512 * 1024:
                raise Error("invalid operation input")
            payload = {**payload, "stdin_sha256": digest(stdin)}
        body = encode(payload)
        if len(body.encode()) > 128 * 1024:
            raise Error("operation too large")

        def work(db):
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM operations WHERE id=?", (operation,)).fetchone()
            if old:
                if (old["worker"], old["kind"], old["payload"]) != (worker, kind, body):
                    raise Error("operation ID already has different contents", 409)
                return operation
            w = db.execute("SELECT * FROM workers WHERE id=? AND enabled=1", (worker,)).fetchone()
            if not w:
                raise Error("unknown or disabled worker", 404)
            if time.time() - w["last_seen"] > 45:
                raise Error("worker offline", 409)
            if kind != "probe" and json.loads(w["info"]).get("mode") != "guest":
                raise Error("worker has no configured guest", 409)
            lease = db.execute("SELECT id FROM leases WHERE worker=?", (worker,)).fetchone()
            if lease and payload.get("lease") != lease[0]:
                raise Error("operation does not own worker lease", 409)
            if payload.get("lease") and (not lease or lease[0] != payload["lease"]):
                raise Error("lease not acquired", 409)
            if kind != "probe" and json.loads(w["info"]).get("lifecycle") and not lease:
                raise Error("lifecycle worker requires a lease", 409)
            if kind in ("guest-prepare", "guest-release") and not json.loads(w["info"]).get("lifecycle"):
                raise Error("worker has no lifecycle support", 409)
            try:
                db.execute("INSERT INTO operations(id,worker,kind,payload,created) VALUES (?,?,?,?,?)", (operation, worker, kind, body, time.time()))
            except sqlite3.IntegrityError:
                raise Error("worker is busy; operation not queued", 409)
            if stdin is not None:
                self.spool(operation, stdin, payload["stdin_sha256"])
            return operation
        return self.tx(work)

    def spool(self, operation, stdin, sha256):
        """operation の stdin を私有の spool に落とす。ロックでやり直した 2 回目は、前の試行が書いた
        同じ中身をそのまま受け入れる（O_EXCL のままだと FileExistsError で submit が落ちる。446）"""
        p = self.input_path(operation)
        p.parent.mkdir(mode=0o700, exist_ok=True)
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if digest(p.read_text(encoding="utf-8")) != sha256:
                raise Error("operation input already spooled with different contents", 409)
            return
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(stdin); f.flush(); os.fsync(f.fileno())

    def heartbeat(self, worker, info):
        if not isinstance(info, dict) or len(encode(info)) > 8192:
            raise Error("invalid worker info")

        def work(db):
            db.execute("UPDATE workers SET last_seen=?,info=? WHERE id=?", (time.time(), encode(info), worker))
            return db.execute("SELECT id FROM operations WHERE worker=? AND state IN ('queued','running') AND cancelled=1", (worker,)).fetchall()
        return {"cancel": [r["id"] for r in self.tx(work)]}

    def poll(self, worker):
        def work(db):
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM operations WHERE worker=? AND state IN ('queued','running') ORDER BY created LIMIT 1", (worker,)).fetchone()
            if not row:
                return {"operation": None}
            db.execute("UPDATE operations SET state='running' WHERE id=?", (row["id"],))
            op = dict(row)
            op["payload"] = json.loads(op["payload"])
            if "stdin_sha256" in op["payload"]:
                op["stdin"] = self.input_path(op["id"]).read_text(encoding="utf-8")
                if digest(op["stdin"]) != op["payload"]["stdin_sha256"]:
                    raise Error("operation input checksum mismatch", 409)
            op["state"] = "running"
            return {"operation": op}
        return self.tx(work)

    @staticmethod
    def owned(db, worker, operation):
        row = db.execute("SELECT * FROM operations WHERE id=? AND worker=?", (operation, worker)).fetchone()
        if not row:
            raise Error("operation not found", 404)
        return row

    def event(self, worker, operation, seq, text):
        if type(seq) is not int or seq < 0 or not isinstance(text, str) or len(text.encode()) > 65536:
            raise Error("invalid event")

        def work(db):
            db.execute("BEGIN IMMEDIATE")
            op = self.owned(db, worker, operation)
            old = db.execute("SELECT text FROM events WHERE op=? AND seq=?", (operation, seq)).fetchone()
            if old:
                if old["text"] != text:
                    raise Error("conflicting event replay", 409)
                return {"ack": seq}
            if op["state"] != "running":
                raise Error("operation is not running", 409)
            row = db.execute("SELECT COUNT(*) n, COALESCE(SUM(length(CAST(text AS BLOB))),0) size FROM events WHERE op=?", (operation,)).fetchone()
            if seq != row["n"]:
                raise Error("event sequence gap", 409)
            if row["size"] + len(text.encode()) > MAX_LOG:
                raise Error("operation log limit exceeded", 413)
            db.execute("INSERT INTO events VALUES (?,?,?)", (operation, seq, text))
            return {"ack": seq}
        return self.tx(work)

    def complete(self, worker, operation, result):
        # "truncated" is optional so an older worker's result stays valid.
        if not isinstance(result, dict) or set(result) - {"truncated"} != {"status", "exit_code", "events"}:
            raise Error("invalid result")
        if "truncated" in result and type(result["truncated"]) is not bool:
            raise Error("invalid truncation flag")
        if result["status"] not in ("succeeded", "failed", "uncertain", "cancelled"):
            raise Error("invalid result status")
        if type(result["events"]) is not int or result["events"] < 0:
            raise Error("invalid event count")
        if result["exit_code"] is not None and type(result["exit_code"]) is not int:
            raise Error("invalid exit code")
        if result["status"] == "succeeded" and result["exit_code"] != 0:
            raise Error("success requires exit code zero")
        if result["status"] == "failed" and (result["exit_code"] is None or result["exit_code"] == 0):
            raise Error("failure requires a nonzero exit code")
        body = encode(result)

        def work(db):
            db.execute("BEGIN IMMEDIATE")
            op = self.owned(db, worker, operation)
            if op["result"]:
                if body != op["result"]:
                    raise Error("conflicting completion replay", 409)
            else:
                if op["state"] != "running":
                    raise Error("operation is not running", 409)
                count = db.execute("SELECT COUNT(*) FROM events WHERE op=?", (operation,)).fetchone()[0]
                if count != result["events"]:
                    raise Error("logs not fully received", 409)
                db.execute("UPDATE operations SET state=?,result=? WHERE id=?", (result["status"], body, operation))
        self.tx(work)
        self.input_path(operation).unlink(missing_ok=True)
        return {"accepted": True}

    def cancel(self, operation):
        self.tx(lambda db: db.execute("UPDATE operations SET cancelled=1 WHERE id=? AND state IN ('queued','running')", (operation,)))

    def resolve(self, operation):
        """Admin has verified the guest stopped; preserve the uncertain result."""
        def work(db):
            if not db.execute("UPDATE operations SET state='resolved' WHERE id=? AND state='uncertain'", (operation,)).rowcount:
                raise Error("operation is not uncertain", 409)
        self.tx(work)

    def operation(self, operation):
        def work(db):
            row = db.execute("SELECT * FROM operations WHERE id=?", (operation,)).fetchone()
            if not row:
                raise Error("operation not found", 404)
            d = dict(row)
            d["payload"] = json.loads(d["payload"])
            d["result"] = json.loads(d["result"]) if d["result"] else None
            d["log"] = "".join(r[0] for r in db.execute("SELECT text FROM events WHERE op=? ORDER BY seq", (operation,)).fetchall())
            return d
        return self.tx(work)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log authorization headers or operation payloads.

    def do_POST(self):
        status, response = 200, {}
        try:
            self.connection.settimeout(15)
            store = self.server.store
            worker = self.headers.get("X-Worker-ID", "")
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            store.authenticate(worker, token)
            if self.headers.get("Transfer-Encoding"):
                raise Error("chunked requests not supported")
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 < n <= MAX_BODY:
                raise Error("invalid request size", 413)
            data = json.loads(self.rfile.read(n))
            if not isinstance(data, dict) or data.pop("version", None) != VERSION:
                raise Error("unsupported protocol version")
            if self.path == "/v1/check":
                response = {"ok": True}
            elif self.path == "/v1/heartbeat":
                response = store.heartbeat(worker, data["info"])
            elif self.path == "/v1/poll":
                response = store.poll(worker)
            elif self.path == "/v1/event":
                response = store.event(worker, data["operation"], data["seq"], data["text"])
            elif self.path == "/v1/complete":
                response = store.complete(worker, data["operation"], data["result"])
            else:
                raise Error("not found", 404)
        except Error as e:
            status, response = e.status, {"error": str(e)}
        except (KeyError, ValueError, TypeError):
            status, response = 400, {"error": "invalid request"}
        except Exception:
            status, response = 500, {"error": "internal error"}
        raw = encode(response).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)


def server(store, host, port, cert=None, key=None):
    if not cert or not key:
        if host not in ("127.0.0.1", "::1"):
            raise Error("TLS certificate and key required outside loopback")
    srv = http.server.ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.store = store
    if cert and key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    return srv
