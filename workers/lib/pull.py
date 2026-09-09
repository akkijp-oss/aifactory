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
        with self.db() as db:
            db.executescript("""
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
            """)
        self.path.chmod(0o600)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def enroll(self, worker):
        if not NAME.fullmatch(worker):
            raise Error("invalid worker name")
        token = secrets.token_urlsafe(32)
        try:
            with self.db() as db:
                db.execute("INSERT INTO workers(id,token_hash) VALUES (?,?)", (worker, digest(token)))
        except sqlite3.IntegrityError:
            raise Error("worker already registered", 409)
        return token

    def authenticate(self, worker, token):
        with self.db() as db:
            row = db.execute("SELECT * FROM workers WHERE id=?", (worker,)).fetchone()
        if not row or not row["enabled"] or not hmac.compare_digest(row["token_hash"], digest(token)):
            raise Error("unauthorized", 401)

    def disable(self, worker):
        with self.db() as db:
            db.execute("UPDATE workers SET enabled=0 WHERE id=?", (worker,))

    def workers(self):
        with self.db() as db:
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

    def acquire(self, worker, lease):
        if not NAME.fullmatch(lease):
            raise Error("invalid lease ID")
        with self.db() as db:
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

    def release_lease(self, worker, lease, operation):
        with self.db() as db:
            op = self.owned(db, worker, operation)
            if op["kind"] != "guest-release" or op["state"] != "succeeded" or json.loads(op["payload"]).get("lease") != lease:
                raise Error("successful guest release required", 409)
            db.execute("DELETE FROM leases WHERE worker=? AND id=?", (worker, lease))

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
        if kind in ("guest-prepare", "guest-release") and (set(payload) != {"lease"} or not NAME.fullmatch(str(payload["lease"]))):
            raise Error("lifecycle requires a lease ID")
        if stdin is not None:
            if kind != "guest-exec" or not isinstance(stdin, str) or len(stdin.encode()) > 512 * 1024:
                raise Error("invalid operation input")
            payload = {**payload, "stdin_sha256": digest(stdin)}
        body = encode(payload)
        if len(body.encode()) > 128 * 1024:
            raise Error("operation too large")
        with self.db() as db:
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
                p = self.input_path(operation)
                p.parent.mkdir(mode=0o700, exist_ok=True)
                fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(stdin); f.flush(); os.fsync(f.fileno())
        return operation

    def heartbeat(self, worker, info):
        if not isinstance(info, dict) or len(encode(info)) > 8192:
            raise Error("invalid worker info")
        with self.db() as db:
            db.execute("UPDATE workers SET last_seen=?,info=? WHERE id=?", (time.time(), encode(info), worker))
            rows = db.execute("SELECT id FROM operations WHERE worker=? AND state IN ('queued','running') AND cancelled=1", (worker,)).fetchall()
        return {"cancel": [r["id"] for r in rows]}

    def poll(self, worker):
        with self.db() as db:
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

    @staticmethod
    def owned(db, worker, operation):
        row = db.execute("SELECT * FROM operations WHERE id=? AND worker=?", (operation, worker)).fetchone()
        if not row:
            raise Error("operation not found", 404)
        return row

    def event(self, worker, operation, seq, text):
        if type(seq) is not int or seq < 0 or not isinstance(text, str) or len(text.encode()) > 65536:
            raise Error("invalid event")
        with self.db() as db:
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
        with self.db() as db:
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
        self.input_path(operation).unlink(missing_ok=True)
        return {"accepted": True}

    def cancel(self, operation):
        with self.db() as db:
            db.execute("UPDATE operations SET cancelled=1 WHERE id=? AND state IN ('queued','running')", (operation,))

    def resolve(self, operation):
        """Admin has verified the guest stopped; preserve the uncertain result."""
        with self.db() as db:
            if not db.execute("UPDATE operations SET state='resolved' WHERE id=? AND state='uncertain'", (operation,)).rowcount:
                raise Error("operation is not uncertain", 409)

    def operation(self, operation):
        with self.db() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (operation,)).fetchone()
            if not row:
                raise Error("operation not found", 404)
            d = dict(row)
            d["payload"] = json.loads(d["payload"])
            d["result"] = json.loads(d["result"]) if d["result"] else None
            d["log"] = "".join(r[0] for r in db.execute("SELECT text FROM events WHERE op=? ORDER BY seq", (operation,)))
            return d


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
