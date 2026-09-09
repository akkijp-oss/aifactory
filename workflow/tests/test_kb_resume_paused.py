"""鍵の利用枠切れで止まった run の、台帳（kb）と配車（dispatch）での扱い（チケット 380 / ADR-0043）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。runner が残す形の state.json を置き、偽 `sandbox` の take を失敗させて短く終える（test_kb_run_from.py と同じ流儀）。

- `failure: "quota"` の run を `kb sync` すると、チケットは **todo** に戻り、メモに「一時停止」「自動再開」と手で打つ `kb run --from` が入る
- `kb resumable --json` が一時停止中のチケットを、解除時刻（retry_after）を過ぎたか（ready）付きで返す。retry_after が無ければ finished から
  AIFACTORY_RESUME_BACKOFF_MIN 分で ready
- 続けて AIFACTORY_RESUME_MAX_HITS 回止まったら todo に戻さず blocked（自動再開は止める）
- `failure: "key"`（鍵そのものが使えない）は blocked。メモに鍵を直してからの `kb run --from`
- `dispatch --resume-paused` は ready なチケットだけを `kb run <id> --from` で回す（runner は `from_step` / `resumed_from` を記録する）。
  ready でなければ何も回さない。通常の `dispatch` も、解除前の一時停止チケットは飛ばす
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
KB = REPO / "kanban" / "bin" / "kb"
DISPATCH = REPO / "glue" / "bin" / "dispatch"
TICKET = "# バグ: 利用枠切れの続き\n\n偽の sandbox で take を失敗させる。\n"

FAKE = r"""#!/usr/bin/env bash
echo "$@" >> "$CALLS"
case "$1" in
  take) echo "FROM_RUN: ${AIFACTORY_FROM_RUN-未設定}" >> "$CALLS"
        echo "[error] pj=$2 に空きなし" >&2; exit 1 ;;
esac
exit 0
"""


def iso(dt):
    return dt.astimezone().isoformat(timespec="seconds")


class KbResumePausedTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.bin = self.ws / "bin"; self.bin.mkdir()
        fake = self.bin / "sandbox"; fake.write_text(FAKE); fake.chmod(0o755)
        (self.ws / "sandbox-state.json").write_text("{}")
        self.calls = self.ws / "calls.log"
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        AIFACTORY_WORKSPACE=str(self.ws), SANDBOX_STATE=str(self.ws / "sandbox-state.json"),
                        CALLS=str(self.calls))
        for k in ("AIFACTORY_FROM_RUN", "AIFACTORY_RESUME_MAX_HITS", "AIFACTORY_RESUME_BACKOFF_MIN"): self.env.pop(k, None)
        self.today = f"{datetime.date.today().isoformat()}-kumitate-931"
        self.prev = "2026-09-06-kumitate-931"
        new = self.kb("new", "kumitate", "bug", "利用枠切れの続き", "--body", "-", "--id", "931", input_text=TICKET)
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)

    def kb(self, *args, input_text=None, env=None):
        return subprocess.run([sys.executable, str(KB), *args], input=input_text, text=True, capture_output=True, env=env or self.env)

    def dispatch(self, *args, env=None):
        return subprocess.run([sys.executable, str(DISPATCH), *args], text=True, capture_output=True, env=env or self.env)

    def paused_run(self, name=None, failure="quota", retry_after="future", hits=1, finished_ago_min=120, **kw):
        """利用枠切れで止まった run の記録（runner が残す形）を置き、チケットの run 欄をそれに向ける"""
        name = name or self.prev
        now = datetime.datetime.now().astimezone()
        if retry_after == "future": retry_after = iso(now + datetime.timedelta(hours=2))
        elif retry_after == "past": retry_after = iso(now - datetime.timedelta(minutes=5))
        d = self.ws / "runs" / name; d.mkdir(parents=True, exist_ok=True)
        (d / "work").mkdir(exist_ok=True); (d / "work" / "plan.md").write_text("# 計画\n", encoding="utf-8")
        fin = now - datetime.timedelta(minutes=finished_ago_min)
        state = {"pj": "kumitate", "task": "931", "workflow": "bug", "branch": "sandbox/931-bug-x", "base": "develop",
                 "result": "human", "next": "human", "pr_url": "", "wip_branch": "sandbox/931-bug-wip", "resume_step": "implement",
                 "failure": failure, "quota_type": "five_hour" if failure == "quota" else None, "retry_after": retry_after,
                 "quota_hits": hits if failure == "quota" else 0,
                 "error": ("implement: 鍵の利用枠の上限で中断（quota・five_hour。解除見込み %s）" % (retry_after or "不明")) if failure == "quota"
                          else "implement: 鍵が使えず中断（key）: Invalid API key · Please run /login",
                 "started": iso(fin - datetime.timedelta(minutes=30)), "finished": iso(fin), "elapsed_s": 1800, "loops": {},
                 "history": [{"step": "plan", "ok": True, "next": "implement", "at": iso(fin)},
                             {"step": "implement", "ok": False, "next": "human", "at": iso(fin), "failure": failure,
                              "quota_type": "five_hour" if failure == "quota" else None, "retry_after": retry_after}]}
        state.update(kw)
        (d / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.kb("set", "931", "--run", name).returncode, 0)
        return name

    def show(self):
        out = self.kb("show", "931").stdout
        return {l.split(None, 1)[0]: l.split(None, 1)[1].strip() for l in out.split("-" * 60)[0].splitlines() if len(l.split(None, 1)) == 2}

    def resumable(self):
        r = self.kb("resumable", "--json"); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    # ---------- kb
    def test_a_quota_stop_goes_back_to_todo_with_the_resume_command(self):
        self.paused_run()
        self.assertEqual(self.kb("sync", "931").returncode, 0)
        s = self.show()
        self.assertEqual(s["status"], "todo")
        self.assertIn("一時停止", s["note"]); self.assertIn("自動再開", s["note"]); self.assertIn("five_hour", s["note"])
        self.assertIn("kb run 931 --from implement --branch sandbox/931-bug-wip", s["note"])

    def test_resumable_says_whether_the_limit_has_reset(self):
        self.paused_run(retry_after="future")
        self.kb("sync", "931")
        plans = self.resumable()
        self.assertEqual([p["id"] for p in plans], [931])
        p = plans[0]
        self.assertFalse(p["ready"]); self.assertEqual(p["step"], "implement"); self.assertEqual(p["branch"], "sandbox/931-bug-wip")
        self.assertEqual(p["until"], p["retry_after"]); self.assertEqual(p["quota_hits"], 1); self.assertFalse(p["hits_exceeded"])
        self.paused_run(retry_after="past")
        self.kb("sync", "931")
        self.assertTrue(self.resumable()[0]["ready"])
        self.assertIn("今すぐ", self.kb("resumable").stdout)

    def test_without_a_reset_time_the_backoff_decides(self):
        self.paused_run(retry_after=None, finished_ago_min=5)
        self.kb("sync", "931")
        self.assertFalse(self.resumable()[0]["ready"])                          # 既定 30 分はまだ
        self.assertIn("解除時刻は不明", self.show()["note"])
        self.paused_run(retry_after=None, finished_ago_min=45)
        self.kb("sync", "931")
        self.assertTrue(self.resumable()[0]["ready"])
        env = dict(self.env, AIFACTORY_RESUME_BACKOFF_MIN="60")
        r = self.kb("resumable", "--json", env=env)
        self.assertFalse(json.loads(r.stdout)[0]["ready"])

    def test_too_many_quota_stops_in_a_row_go_to_a_human(self):
        self.paused_run(hits=6)
        self.assertEqual(self.kb("sync", "931").returncode, 0)
        s = self.show()
        self.assertEqual(s["status"], "blocked")
        self.assertIn("6 回続けて", s["note"]); self.assertIn("kb run 931 --from implement", s["note"])
        self.assertEqual(self.resumable(), [])                                 # blocked は対象外
        env = dict(self.env, AIFACTORY_RESUME_MAX_HITS="10")
        self.assertEqual(self.kb("sync", "931", env=env).returncode, 0)
        self.assertEqual(self.show()["status"], "todo")

    def test_a_dead_key_is_for_a_human(self):
        self.paused_run(failure="key", retry_after=None)
        self.assertEqual(self.kb("sync", "931").returncode, 0)
        s = self.show()
        self.assertEqual(s["status"], "blocked")
        self.assertIn("鍵が使えず", s["note"]); self.assertIn("Invalid API key", s["note"])
        self.assertIn("kb run 931 --from implement --branch sandbox/931-bug-wip", s["note"])
        self.assertEqual(self.resumable(), [])

    def test_a_quota_stop_without_a_wip_branch_is_for_a_human(self):
        """wip の push に失敗した run は続きを機械で回せない（初めからやり直す配車もしない）。人が wip.patch を見る"""
        self.paused_run(wip_branch="")
        self.assertEqual(self.kb("sync", "931").returncode, 0)
        s = self.show()
        self.assertEqual(s["status"], "blocked"); self.assertIn("続きに要る記録", s["note"])
        self.assertEqual(self.resumable(), [])

    def test_a_plain_human_stop_is_not_resumable(self):
        """従来の human（review の FAIL 等）は blocked のまま。自動再開の対象にしない"""
        self.paused_run(failure=None, retry_after=None, quota_type=None, quota_hits=0)
        self.kb("sync", "931")
        self.assertEqual(self.show()["status"], "blocked")
        self.assertEqual(self.resumable(), [])

    # ---------- dispatch
    def test_dispatch_resume_paused_continues_a_ready_ticket_from_the_wip(self):
        self.paused_run(retry_after="past")
        self.kb("sync", "931")
        r = self.dispatch("--resume-paused")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("続き: implement から origin/sandbox/931-bug-wip", r.stdout)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertIn(f"FROM_RUN: {self.prev}", calls)                         # runner は前回の run を受け取っている
        st = json.loads((self.ws / "runs" / self.today / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(st["from_step"], "implement"); self.assertEqual(st["from_branch"], "sandbox/931-bug-wip")
        self.assertEqual(st["resumed_from"], self.prev); self.assertEqual(st["quota_hits"], 1)
        log = (self.ws / "logs" / "dispatch.log").read_text(encoding="utf-8")
        self.assertIn("start 931", log); self.assertIn("end   931", log)

    def test_dispatch_resume_paused_waits_until_the_limit_resets(self):
        self.paused_run(retry_after="future")
        self.kb("sync", "931")
        r = self.dispatch("--resume-paused")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("今回せるものは無い", r.stdout); self.assertIn("931", r.stdout)
        self.assertFalse(self.calls.exists(), "runner を呼ばないこと")
        self.assertFalse((self.ws / "logs" / "dispatch.log").exists(), "回さなかった tick は配車ログに残さない")
        self.assertEqual(self.show()["status"], "todo")

    def test_dispatch_resume_paused_leaves_other_todos_alone(self):
        """一時停止中でない todo（普通の未着手）は --resume-paused では回さない"""
        self.assertEqual(self.show()["status"], "todo")                      # run の無い、ただの todo
        r = self.dispatch("--resume-paused")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_plain_dispatch_skips_a_paused_ticket_before_the_reset(self):
        self.paused_run(retry_after="future")
        self.kb("sync", "931")
        r = self.dispatch("--once")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("一時停止中", r.stdout); self.assertIn("飛ばす", r.stdout)
        self.assertFalse(self.calls.exists())

    def test_plain_dispatch_continues_a_paused_ticket_after_the_reset(self):
        """通常の配車でも、解除後の一時停止チケットは初めからではなく続き（--from）で回す"""
        self.paused_run(retry_after="past")
        self.kb("sync", "931")
        r = self.dispatch("--once")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("続き: implement", r.stdout)
        st = json.loads((self.ws / "runs" / self.today / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(st["from_step"], "implement")


if __name__ == "__main__":
    unittest.main()
