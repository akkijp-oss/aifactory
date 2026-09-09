import concurrent.futures
import json
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
from pull import Error, Store, server


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(pathlib.Path(self.tmp.name) / "queue.db")
        self.token = self.store.enroll("mac1")
        self.store.enroll("mac2")
        self.store.heartbeat("mac1", {"mode": "guest"})

    def submit(self):
        op = self.store.submit("mac1", "probe", {})
        self.store.poll("mac1")
        return op

    def test_authentication_and_revocation(self):
        self.store.authenticate("mac1", self.token)
        for worker, token in [("mac2", self.token), ("mac1", "wrong")]:
            with self.assertRaises(Error): self.store.authenticate(worker, token)
        self.store.disable("mac1")
        with self.assertRaises(Error): self.store.authenticate("mac1", self.token)

    def test_offline_and_probe_only_refuse_guest(self):
        with self.assertRaises(Error): self.store.submit("mac2", "probe", {})
        self.store.heartbeat("mac1", {"mode": "probe-only"})
        with self.assertRaises(Error): self.store.submit("mac1", "guest-exec", {"command": "true"})

    def test_timeout_is_normalized_and_validated(self):
        for value in [True, 0, 3601, "1"]:
            with self.assertRaises(Error): self.store.submit("mac1", "guest-exec", {"command": "true", "timeout": value})
        op = self.store.submit("mac1", "guest-exec", {"command": "true"})
        self.assertEqual(self.store.operation(op)["payload"]["timeout"], 300)

    def test_concurrent_reservation_has_single_winner(self):
        def submit(_):
            try: return self.store.submit("mac1", "probe", {})
            except Error: return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, range(8)))
        self.assertEqual(sum(bool(x) for x in results), 1)

    def test_idempotent_submit_and_conflicting_contents(self):
        op = self.store.submit("mac1", "probe", {}, "stable-id")
        self.assertEqual(op, self.store.submit("mac1", "probe", {}, "stable-id"))
        with self.assertRaises(Error): self.store.submit("mac1", "guest-exec", {"command": "true"}, "stable-id")

    def test_worker_isolation_and_event_replay(self):
        op = self.submit()
        with self.assertRaises(Error): self.store.event("mac2", op, 0, "private")
        with self.assertRaises(Error): self.store.event("mac1", op, 1, "gap")
        self.assertEqual(self.store.event("mac1", op, 0, "日本語\n"), {"ack": 0})
        self.store.event("mac1", op, 0, "日本語\n")
        with self.assertRaises(Error): self.store.event("mac1", op, 0, "different")
        self.assertEqual(self.store.operation(op)["log"], "日本語\n")

    def test_completion_waits_for_logs_and_is_idempotent(self):
        op = self.submit()
        result = {"status": "succeeded", "exit_code": 0, "events": 1}
        with self.assertRaises(Error): self.store.complete("mac1", op, result)
        self.store.event("mac1", op, 0, "ok")
        self.store.complete("mac1", op, result)
        self.store.complete("mac1", op, result)
        with self.assertRaises(Error): self.store.complete("mac1", op, {**result, "status": "failed"})
        self.assertEqual(self.store.operation(op)["state"], "succeeded")

    def test_truncated_result_is_accepted_and_unknown_keys_are_not(self):
        op = self.submit()
        self.store.complete("mac1", op, {"status": "succeeded", "exit_code": 0, "events": 0, "truncated": True})
        self.assertEqual(self.store.operation(op)["state"], "succeeded")
        other = self.submit()
        with self.assertRaises(Error):
            self.store.complete("mac1", other, {"status": "succeeded", "exit_code": 0, "events": 0, "truncated": "yes"})
        with self.assertRaises(Error):
            self.store.complete("mac1", other, {"status": "succeeded", "exit_code": 0, "events": 0, "surprise": 1})

    def test_uncertain_reserves_worker_until_operator_resolution(self):
        op = self.submit()
        self.store.complete("mac1", op, {"status": "uncertain", "exit_code": None, "events": 0})
        with self.assertRaises(Error): self.store.submit("mac1", "probe", {})
        self.assertEqual(self.store.workers()[0]["operation"]["state"], "uncertain")
        self.store.resolve(op)
        self.store.submit("mac1", "probe", {})

    def test_restart_does_not_reassign_running_work(self):
        op = self.submit()
        reopened = Store(self.store.path)
        self.assertEqual(reopened.poll("mac1")["operation"]["id"], op)
        self.assertIsNone(reopened.poll("mac2")["operation"])
        reopened.cancel(op)
        self.assertEqual(reopened.heartbeat("mac1", {})["cancel"], [op])

    def test_http_auth_version_and_forbidden_api(self):
        srv = server(self.store, "127.0.0.1", 0)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        def post(path, body, token):
            req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}" + path,
                json.dumps(body).encode(), headers={"Authorization": "Bearer " + token, "X-Worker-ID": "mac1"})
            with urllib.request.urlopen(req) as r: return json.load(r)
        with self.assertRaises(urllib.error.HTTPError) as cm: post("/v1/poll", {"version": 1}, "wrong")
        self.assertEqual(cm.exception.code, 401)
        with self.assertRaises(urllib.error.HTTPError) as cm: post("/v1/poll", {"version": 2}, self.token)
        self.assertEqual(cm.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as cm: post("/v1/submit", {"version": 1}, self.token)
        self.assertEqual(cm.exception.code, 404)
        before = self.store.workers()
        self.assertEqual(post("/v1/check", {"version": 1}, self.token), {"ok": True})
        self.assertEqual(self.store.workers(), before)
        with self.assertRaises(urllib.error.HTTPError) as cm: post("/v1/check", {"version": 1}, "wrong")
        self.assertEqual(cm.exception.code, 401)
        self.assertEqual(post("/v1/poll", {"version": 1}, self.token), {"operation": None})

    def test_nonloopback_requires_tls(self):
        with self.assertRaises(Error): server(self.store, "0.0.0.0", 0)

    def test_lease_blocks_other_runs_between_operations(self):
        self.store.heartbeat("mac1", {"mode": "guest", "lifecycle": True})
        self.store.acquire("mac1", "run-one")
        self.store.acquire("mac1", "run-one")
        with self.assertRaises(Error): self.store.acquire("mac1", "run-two")
        with self.assertRaises(Error): self.store.submit("mac1", "probe", {})
        with self.assertRaises(Error): self.store.submit("mac1", "guest-exec", {"command":"true", "lease":"run-two"})
        op=self.store.submit("mac1", "guest-exec", {"command":"true", "lease":"run-one"})
        self.store.poll("mac1")
        self.store.complete("mac1", op, {"status":"succeeded", "exit_code":0, "events":0})
        with self.assertRaises(Error): self.store.acquire("mac1", "run-two")
        with self.assertRaises(Error): self.store.release_lease("mac1", "run-one", op)
        release=self.store.submit("mac1", "guest-release", {"lease":"run-one"})
        self.store.poll("mac1")
        self.store.complete("mac1", release, {"status":"succeeded", "exit_code":0, "events":0})
        self.store.release_lease("mac1", "run-one", release)
        self.store.acquire("mac1", "run-two")
        self.store.release_lease("mac1", "run-one", release)
        self.assertEqual(self.store.workers()[0]['lease']['id'], 'run-two')

    def test_secret_input_is_not_in_operation_or_database_and_is_removed(self):
        secret="export TOKEN='private-value'\n"
        op=self.store.submit("mac1", "guest-exec", {"command":"cat > credentials"}, stdin=secret)
        self.assertNotIn(secret, json.dumps(self.store.operation(op)))
        self.assertNotIn(secret.encode(), self.store.path.read_bytes())
        self.assertEqual(self.store.input_path(op).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(Error): self.store.event("mac2",op,0,"bad")
        self.assertIsNone(self.store.poll("mac2")["operation"])
        self.assertEqual(self.store.poll("mac1")["operation"]["stdin"], secret)
        self.store.complete("mac1",op,{"status":"succeeded","exit_code":0,"events":0})
        self.assertFalse(self.store.input_path(op).exists())
        self.store.complete("mac1",op,{"status":"succeeded","exit_code":0,"events":0})

    def test_input_checksum_and_idempotency(self):
        op=self.store.submit("mac1", "guest-exec", {"command":"cat"}, "input-id", stdin="hello")
        self.assertEqual(op,self.store.submit("mac1", "guest-exec", {"command":"cat"}, "input-id", stdin="hello"))
        with self.assertRaises(Error): self.store.submit("mac1", "guest-exec", {"command":"cat"}, "input-id", stdin="changed")
        self.store.input_path(op).write_text("corrupt")
        with self.assertRaises(Error): self.store.poll("mac1")

    def test_lifecycle_requires_capable_worker_and_explicit_lease(self):
        with self.assertRaises(Error): self.store.acquire("mac1", "run-one")
        with self.assertRaises(Error): self.store.submit("mac1", "guest-prepare", {"lease":"run-one"})
        self.store.heartbeat("mac1", {"mode":"guest", "lifecycle":True})
        with self.assertRaises(Error): self.store.submit("mac1", "guest-exec", {"command":"true"})

    def test_base_image_must_be_ready_before_reservation(self):
        self.store.heartbeat("mac1", {"mode":"guest", "lifecycle":True, "base_ready":False})
        with self.assertRaises(Error): self.store.acquire("mac1", "run-one")
        self.assertIsNone(self.store.workers()[0]['lease'])
        self.store.heartbeat("mac1", {"mode":"guest", "lifecycle":True, "base_ready":True})
        self.store.acquire("mac1", "run-one")


    def test_network_setup_must_be_ready_before_reservation(self):
        self.store.heartbeat("mac1", {"mode":"guest", "lifecycle":True, "base_ready":True, "network_ready":False})
        with self.assertRaisesRegex(Error, "network setup"):
            self.store.acquire("mac1", "run-one")
        self.assertIsNone(self.store.workers()[0]['lease'])
        self.store.heartbeat("mac1", {"mode":"guest", "lifecycle":True, "base_ready":True, "network_ready":True})
        self.store.acquire("mac1", "run-one")


if __name__ == "__main__": unittest.main()
