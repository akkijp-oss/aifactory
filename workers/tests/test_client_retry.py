"""runner 側の poll が制御系 sqlite の locked で op を捨てないこと（チケット 446）。

  python3 -m unittest discover -s workers/tests -v

Client.execute は op の状態を 1 秒ごとに読む（40 分の agent step なら 2400 回）。ここで 1 回でも
database is locked を食うと、旧実装は except BaseException で op を cancel していた。cancel は
worker の heartbeat 経由でゲストごと停止させる（未 push の実装が消える）ので、一時的なロックでは
cancel しない。偽 store で store だけ差し替え、guest も Mac も使わずに固定する。
"""
import pathlib
import sqlite3
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
import client
from client import Client


class FakeStore:
    """Store の顔だけ真似る。operation() が locked を投げる回数を仕込める"""

    def __init__(self, locked_reads=0, cancel_error=None):
        self.locked_reads = locked_reads
        self.cancel_error = cancel_error
        self.reads = self.cancels = 0
        self.state = "running"

    def submit(self, worker, kind, payload, stdin=None):
        return "op-1"

    def operation(self, op):
        self.reads += 1
        if self.locked_reads > 0:
            self.locked_reads -= 1
            raise sqlite3.OperationalError("database is locked")
        state = self.state if self.reads < 3 else "succeeded"
        return {"state": state, "log": "hello\n", "result": {"exit_code": 0}}

    def cancel(self, op):
        self.cancels += 1
        if self.cancel_error: raise self.cancel_error


class Clock:
    """poll の 1 秒待ちを実時間で待たないための時計（sleep したぶんだけ進む）"""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class ClientRetryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        self.clock = Clock()
        patch = unittest.mock.patch.object(client, "time", self.clock)
        patch.start()
        self.addCleanup(patch.stop)

    def build(self, store):
        c = Client.__new__(Client)
        c.store, c.worker, c.lease, c.run_dir = store, "mac1", "run-1", self.dir
        return c

    def test_a_locked_read_is_retried_instead_of_cancelling_the_operation(self):
        """読みが 1 回 locked でも op を取り消さず、最後まで読み切る"""
        store = FakeStore(locked_reads=1)
        c = self.build(store)
        op, done = c.execute("guest-exec", {"command": "true"})
        self.assertEqual((op, done.returncode, done.stdout), ("op-1", 0, "hello\n"))
        self.assertEqual(store.cancels, 0)                       # ここが本題: ゲストを止めない
        self.assertIn("locked", (self.dir / "worker-operations.log").read_text())

    def test_a_lock_that_never_clears_still_fails_the_step(self):
        """ずっと locked のままなら、予算を使い切って従来どおり失敗する（黙って待ち続けない）"""
        store = FakeStore(locked_reads=10**6)
        c = self.build(store)
        with self.assertRaises(sqlite3.OperationalError):
            c.execute("guest-exec", {"command": "true"})
        self.assertLessEqual(store.reads, client.POLL_LOCK_BUDGET_S + 2)

    def test_a_failing_cancel_does_not_hide_the_original_error(self):
        """cancel 自体が落ちても（制御系 DB が掴めない）、元の例外を上げる"""
        store = FakeStore(cancel_error=sqlite3.OperationalError("database is locked"))
        store.state = "cancelled"                                # succeeded でも failed でもない終わり方
        c = self.build(store)
        with self.assertRaises(RuntimeError) as caught:
            c.execute("guest-exec", {"command": "true"})
        self.assertIn("cancelled", str(caught.exception))
        self.assertEqual(store.cancels, 1)


if __name__ == "__main__":
    unittest.main()
