"""Control-plane runner transport. Only the worker initiates a network connection."""
import pathlib
import sqlite3
import subprocess
import time
from pull import Store, locked

# 制御系 DB が掴めないあいだ poll を続ける上限（チケット 446）。Store 側のやり直し（tx）を抜けてきた
# locked だけがここに来る。op そのものは worker の中で無事に走っているので、runner の読みが
# 一時的にできないことで op を取り消してはいけない（取り消すと worker がゲストごと止める）
POLL_LOCK_BUDGET_S = 120


class Client:
    def __init__(self, db, worker, lease, run_dir):
        self.store = Store(db)
        self.worker, self.lease = worker, lease
        self.run_dir = pathlib.Path(run_dir)

    def note(self, line):
        """この run が出した操作の記録。次に locked が起きたとき「待ち切れ」か「即時」かを人が読める"""
        with (self.run_dir / "worker-operations.log").open("a") as f:
            f.write(line + "\n")

    def execute(self, kind, payload=None, stdin=None, emit=None):
        payload = {**(payload or {}), "lease": self.lease}
        op = self.store.submit(self.worker, kind, payload, stdin=stdin)
        self.note(f"{op} {kind}")
        deadline = time.monotonic() + payload.get("timeout", 600) + 120
        locked_since = None
        seen = 0
        try:
            while True:
                try:
                    row = self.store.operation(op)
                except sqlite3.OperationalError as e:
                    # 制御系 DB のロック。op の失敗ではないので捨てず、予算いっぱいまで読み直す
                    if not locked(e): raise
                    if locked_since is None:
                        locked_since = time.monotonic()
                        self.note(f"{op} control database locked; retrying up to {POLL_LOCK_BUDGET_S}s: {e}")
                    waited = time.monotonic() - locked_since
                    if waited > POLL_LOCK_BUDGET_S:
                        self.note(f"{op} control database locked for {waited:.0f}s; giving up")
                        raise
                    time.sleep(1)
                    continue
                if locked_since is not None:
                    self.note(f"{op} control database readable again after {time.monotonic() - locked_since:.0f}s")
                locked_since = None
                if emit and len(row["log"]) > seen:
                    emit(row["log"][seen:])
                seen = len(row["log"])
                if row["state"] not in ("queued", "running"):
                    if row["state"] not in ("succeeded", "failed"):
                        raise RuntimeError(f"Worker operation {op}: {row['state']}; lease retained")
                    rc = (row["result"] or {}).get("exit_code")
                    if rc is None or (row["state"] == "failed" and rc == 0):
                        raise RuntimeError(f"Worker operation {op}: invalid exit result; lease retained")
                    return op, subprocess.CompletedProcess(kind, rc, row["log"], "")
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Worker operation {op}: timeout; lease retained")
                time.sleep(1)
        except BaseException:
            # 取り消しそのものが落ちても（制御系 DB が掴めない等）、元の失敗を隠さない
            try:
                self.store.cancel(op)
            except Exception as e:
                self.note(f"{op} cancel failed: {e}")
            raise
