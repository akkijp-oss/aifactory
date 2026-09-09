"""control submit argument handling: --lease auto / --lease <id> / --command / --wait."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import tempfile
import threading
import time
import unittest

PATH = pathlib.Path(__file__).resolve().parents[1] / "bin/control"
spec = importlib.util.spec_from_file_location("control", PATH, loader=importlib.machinery.SourceFileLoader("control", str(PATH)))
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class ControlSubmitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = pathlib.Path(self.tmp.name) / "queue.db"
        self.store = control.Store(self.db)
        self.store.enroll("mac1")
        self.store.heartbeat("mac1", {"mode": "guest", "lifecycle": True})

    def run_control(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = control.main(["--db", str(self.db), *args])
        self.err = err.getvalue()
        return code, out.getvalue()

    def payload_file(self, payload):
        path = pathlib.Path(self.tmp.name) / "payload.json"
        path.write_text(json.dumps(payload))
        return str(path)

    def test_command_submits_without_a_payload_file_and_defaults_timeout(self):
        self.store.acquire("mac1", "run-1")
        code, out = self.run_control("submit", "mac1", "guest-exec", "--lease", "auto", "--command", "pgrep -fl claude")
        self.assertEqual(code, 0)
        payload = self.store.operation(out.strip())["payload"]
        self.assertEqual(payload, {"command": "pgrep -fl claude", "timeout": 60, "lease": "run-1"})

    def test_explicit_lease_id_goes_into_the_payload_and_overrides_the_file(self):
        self.store.acquire("mac1", "run-2")
        code, out = self.run_control("submit", "mac1", "guest-exec", "--lease", "run-2", "--command", "true",
                                     "--payload-file", self.payload_file({"lease": "stale", "timeout": 30}))
        self.assertEqual(code, 0)
        self.assertEqual(self.store.operation(out.strip())["payload"],
                         {"command": "true", "timeout": 30, "lease": "run-2"})

    def test_payload_file_lease_wins_over_auto(self):
        self.store.acquire("mac1", "run-3")
        code, out = self.run_control("submit", "mac1", "guest-exec", "--lease", "auto",
                                     "--payload-file", self.payload_file({"command": "true", "lease": "run-3"}))
        self.assertEqual(code, 0)
        self.assertEqual(self.store.operation(out.strip())["payload"]["lease"], "run-3")

    def test_auto_without_a_lease_still_raises_the_lifecycle_error(self):
        code, out = self.run_control("submit", "mac1", "guest-exec", "--lease", "auto", "--command", "true")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("lifecycle worker requires a lease", self.err)

    def test_wait_prints_the_show_json_of_the_finished_operation(self):
        self.store.acquire("mac1", "run-4")
        done = threading.Event()

        def worker():
            for _ in range(200):
                if self.store.poll("mac1")["operation"]:
                    break
                time.sleep(0.01)
            self.store.event("mac1", "op-wait", 0, "hello\n")
            self.store.complete("mac1", "op-wait", {"status": "succeeded", "exit_code": 0, "events": 1})
            done.set()

        thread = threading.Thread(target=worker)
        thread.start()
        self.addCleanup(thread.join)
        code, out = self.run_control("submit", "mac1", "guest-exec", "--lease", "auto", "--command", "true",
                                     "--wait", "30", "--operation-id", "op-wait")
        self.assertTrue(done.is_set())
        record = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual((record["id"], record["state"], record["log"]), ("op-wait", "succeeded", "hello\n"))
        self.assertEqual(record, self.store.operation("op-wait"))

    def test_wait_that_expires_prints_the_pending_record_and_fails(self):
        self.store.acquire("mac1", "run-5")
        control.WAIT_POLL_SECONDS = 0.01
        self.addCleanup(setattr, control, "WAIT_POLL_SECONDS", 0.5)
        code, out = self.run_control("submit", "mac1", "guest-exec", "--lease", "auto", "--command", "true", "--wait", "1")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["state"], "queued")
        self.assertIn("timed out waiting for", self.err)


if __name__ == "__main__":
    unittest.main()
