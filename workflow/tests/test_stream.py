"""runner の逐次ログ（ADR-0014）のテスト。VM も claude も使わない。

  python3 -m unittest discover -s workflow/tests -v
"""
import importlib.machinery, importlib.util, json, pathlib, tempfile, time, unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_loader("run_mod", importlib.machinery.SourceFileLoader("run_mod", str(REPO / "workflow" / "bin" / "run")))
run = importlib.util.module_from_spec(spec); spec.loader.exec_module(run)

EVENTS = [
    {"type": "system", "subtype": "hook_started", "hook_name": "x"},
    {"type": "system", "subtype": "init", "model": "claude-haiku-4-5-20251001", "tools": ["Bash", "Read"], "cwd": "/home/dev/app"},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "調べます。"}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "echo a\necho b", "description": "x"}}]}},
    {"type": "rate_limit_event", "rate_limit_info": {}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "a\nb\nc\nd", "is_error": False}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/home/dev/app/README.md"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": [{"type": "text", "text": "line1"}], "is_error": True}]}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}},
    {"type": "result", "subtype": "success", "result": "Done.", "num_turns": 3, "duration_ms": 6500, "total_cost_usd": 0.0234},
]


class RendererTest(unittest.TestCase):
    def render_all(self, events, t0=None):
        r = run.EventRenderer(t0 if t0 is not None else time.time() - 65)
        out = [r(json.dumps(e, ensure_ascii=False) + "\n") for e in events]
        return r, [o for o in out if o is not None]

    def test_events_become_readable_lines(self):
        r, lines = self.render_all(EVENTS)
        text = "\n".join(lines)
        self.assertEqual(len(r.raw), len(EVENTS))                                  # 生は全部残る
        self.assertIn("init model=claude-haiku-4-5-20251001 tools=2 cwd=/home/dev/app", text)
        self.assertIn("▶ Bash: echo a⏎echo b", text)                                # 改行は ⏎ に
        self.assertIn("▶ Read: /home/dev/app/README.md", text)
        self.assertIn("    a\n    b\n    c\n    …(+1 行)", text)                    # 結果は先頭 3 行
        self.assertIn("↳ ERROR\n    line1", text)
        self.assertIn("result: success turns=3 duration=6s cost=$0.02", text)
        self.assertNotIn("result: success turns=3 duration=6s cost=$0.02\nDone.", text)   # 直前の assistant 文と同じなら繰り返さない
        self.assertNotIn("hook_started", text); self.assertNotIn("rate_limit", text)
        self.assertTrue(all(l.startswith("[+01:05]") or l.startswith("  ↳") for l in lines), lines)

    def test_result_body_kept_when_different(self):
        _, lines = self.render_all([{"type": "result", "subtype": "success", "result": "最終報告", "num_turns": 1, "duration_ms": 1000}])
        self.assertEqual(lines[0].split("\n")[1], "最終報告")

    def test_non_json_passthrough_and_blank(self):
        r = run.EventRenderer(time.time())
        self.assertIsNone(r("\n")); self.assertIsNone(r("   \n"))
        self.assertTrue(r("Error: something\n").endswith("Error: something"))
        self.assertEqual(r.raw, [])

    def test_short(self):
        self.assertEqual(run._short("x" * 10, 4), "xxxx…(+6)"); self.assertEqual(run._short("a\nb"), "a⏎b")


class StreamTest(unittest.TestCase):
    def test_stream_writes_incrementally_and_returns_rc(self):
        with tempfile.TemporaryDirectory() as d:
            log = pathlib.Path(d) / "x.log"
            rc, out = run.stream("printf 'x\\ny\\n'; echo err >&2; exit 3", log)
            self.assertEqual(rc, 3); self.assertEqual(out, "x\ny\nerr\n"); self.assertEqual(log.read_text(), "x\ny\nerr\n")
            rc, out = run.stream(["bash", "-c", "echo more"], log)                 # 追記
            self.assertEqual(rc, 0); self.assertEqual(log.read_text(), "x\ny\nerr\nmore\n")

    def test_stream_with_renderer(self):
        with tempfile.TemporaryDirectory() as d:
            log = pathlib.Path(d) / "x.log"
            r = run.EventRenderer(time.time())
            rc, out = run.stream(["bash", "-c", "printf '%s\\n%s\\n' '{\"type\":\"result\",\"subtype\":\"success\",\"result\":\"ok\"}' 'not json'"], log, render=r)
            self.assertEqual(rc, 0); self.assertIn("result: success", out); self.assertIn("not json", out); self.assertEqual(len(r.raw), 1)

    def test_stream_invalid_utf8_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            log = pathlib.Path(d) / "x.log"
            rc, out = run.stream(["bash", "-c", "printf '\\xff\\xfe bad\\n'"], log)
            self.assertEqual(rc, 0); self.assertIn("bad", out)


if __name__ == "__main__":
    unittest.main()
