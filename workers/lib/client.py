"""Control-plane runner transport. Only the worker initiates a network connection."""
import pathlib
import subprocess
import time
from pull import Store


class Client:
    def __init__(self, db, worker, lease, run_dir):
        self.store = Store(db)
        self.worker, self.lease = worker, lease
        self.run_dir = pathlib.Path(run_dir)

    def execute(self, kind, payload=None, stdin=None, emit=None):
        payload = {**(payload or {}), "lease": self.lease}
        op = self.store.submit(self.worker, kind, payload, stdin=stdin)
        with (self.run_dir / "worker-operations.log").open("a") as f:
            f.write(f"{op} {kind}\n")
        deadline = time.monotonic() + payload.get("timeout", 600) + 120
        seen = 0
        try:
            while True:
                row = self.store.operation(op)
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
            self.store.cancel(op)
            raise
