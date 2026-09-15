"""配車の下見（`GET /api/next` → `core.ticket_next`）が、`dispatch` が実際に回す票と一致すること（#581）。

  python3 -m unittest discover -s console/tests -p 'test_next_preview.py' -v

`kb next` は todo を id 順に 1 件返すだけで、先行条件も一時停止も見ない（ADR-0077 / ADR-0078 が「薄いまま」と決めている）。
一方 `dispatch` は未完了の先行票を持つ票（#573 / ADR-0078）と、解除前の一時停止（ADR-0043 / ADR-0046）を飛ばす。
下見が `kb next` の生の返りをそのまま見せていたので、★画面が「次はこれ」と見せた票を dispatch が飛ばしていた。

このテストは**下見と dispatch に同じ板を見せて、選んだ id が同じであること**を確かめる。
VM も claude も使わない: 偽の `sandbox` を PATH に置いて take を失敗させ、run は即座に終わる
（workflow/tests/test_kb_resume_paused.py と同じ流儀）。一時停止の state.json も同じ形で置く。
"""
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
KB = REPO / "kanban" / "bin" / "kb"
DISPATCH = REPO / "glue" / "bin" / "dispatch"
PJ = "kumitate"
BODY = "x\n\n## 完了条件\n- y\n"

# take を失敗させる偽 sandbox（VM を取らずに run が終わる）
FAKE = r"""#!/usr/bin/env bash
echo "$@" >> "$CALLS"
case "$1" in
  take) echo "[error] pj=$2 に空きなし" >&2; exit 1 ;;
esac
exit 0
"""

PREAMBLE = '''
import json, sys
sys.path.insert(0, %r)
import core
def out(v): print(json.dumps(v, ensure_ascii=False, default=str))
''' % str(REPO / "console" / "lib")


def iso(dt):
    return dt.astimezone().isoformat(timespec="seconds")


class NextPreviewTest(unittest.TestCase):
    """下見（core.ticket_next）と配車（glue/bin/dispatch）が同じ票を選ぶ"""

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        b = self.ws / "bin"; b.mkdir()
        fake = b / "sandbox"; fake.write_text(FAKE); fake.chmod(0o755)
        (self.ws / "sandbox-state.json").write_text("{}")
        self.env = dict(os.environ, PATH=f"{b}:{os.environ['PATH']}", AIFACTORY_WORKSPACE=str(self.ws),
                        SANDBOX_STATE=str(self.ws / "sandbox-state.json"), CALLS=str(self.ws / "calls.log"),
                        CONSOLE_JOBS=str(self.ws / "jobs"))
        for k in ("AIFACTORY_FROM_RUN", "AIFACTORY_RESUME_MAX_HITS", "AIFACTORY_RESUME_BACKOFF_MIN"): self.env.pop(k, None)

    # ---------- 板を組む
    def kb(self, *args, stdin=None, ok=True):
        r = subprocess.run([sys.executable, str(KB), *args], input=stdin, text=True, capture_output=True, env=self.env)
        if ok: self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def new(self, tid, *extra):
        self.kb("new", PJ, "feature", f"テスト票 {tid}", "--body", "-", "--id", str(tid), *extra, stdin=BODY)

    def paused(self, tid, retry_after="future", failure="quota", hits=1, finished_ago_min=120, sync_env=None):
        """利用枠切れ（または鍵なし）で止まった run を置き、その票を一時停止中にする（kb resumable が拾う形）"""
        name = f"2026-09-06-{PJ}-{tid}"
        now = datetime.datetime.now().astimezone()
        if retry_after == "future": retry_after = iso(now + datetime.timedelta(hours=2))
        elif retry_after == "past": retry_after = iso(now - datetime.timedelta(minutes=5))
        d = self.ws / "runs" / name; (d / "work").mkdir(parents=True, exist_ok=True)
        fin = now - datetime.timedelta(minutes=finished_ago_min)
        state = {"pj": PJ, "task": str(tid), "workflow": "feature", "branch": f"sandbox/{tid}-feature-x", "base": "develop",
                 "result": "human" if failure == "quota" else "failed", "next": "human", "pr_url": "",
                 "wip_branch": f"sandbox/{tid}-feature-wip", "resume_step": "implement", "failure": failure,
                 "quota_type": "five_hour" if failure == "quota" else None, "retry_after": retry_after,
                 "quota_hits": hits if failure == "quota" else 0, "needed_keys": ["fable"] if failure == "nokey" else [],
                 "error": "implement: 中断", "started": iso(fin - datetime.timedelta(minutes=30)), "finished": iso(fin),
                 "elapsed_s": 1800, "loops": {}, "history": []}
        (d / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        self.kb("set", str(tid), "--run", name)
        env0 = self.env
        if sync_env: self.env = dict(self.env, **sync_env)
        try: self.kb("sync", str(tid))
        finally: self.env = env0
        self.assertEqual(self.show(tid)["status"], "todo", "一時停止は todo に戻っているのが前提")
        return name

    def show(self, tid):
        out = self.kb("show", str(tid)).stdout
        return {l.split(None, 1)[0]: l.split(None, 1)[1].strip() for l in out.split("-" * 60)[0].splitlines() if len(l.split(None, 1)) == 2}

    # ---------- 下見と配車
    def preview(self, pj=PJ):
        """console の下見（GET /api/next が返すもの）を別プロセスで読む"""
        code = PREAMBLE + ("out(core.ticket_next(pj=%r))\n" % pj)
        r = subprocess.run([sys.executable, "-c", code], env=self.env, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def kb_next(self, pj=PJ):
        """薄いままの `kb next`（依存も一時停止も見ない。ADR-0077 / ADR-0078）"""
        out = self.kb("next", "--pj", pj, "--json").stdout.strip()
        return json.loads(out) if out else None

    def dispatched(self, *args):
        """dispatch --once が実際に回した票の id（回さなければ None）と、その標準出力"""
        r = subprocess.run([sys.executable, str(DISPATCH), "--once", "--pj", PJ, *args], text=True, capture_output=True, env=self.env)
        m = re.search(r"^\[dispatch\] start (\d+) ", r.stdout, re.M)
        return (int(m.group(1)) if m else None), r.stdout

    def assertSamePick(self, extra_msg=""):
        """★本票の完了条件: 下見が見せる票と、dispatch が実際に回す票が同じ"""
        p = self.preview()
        picked, out = self.dispatched()
        self.assertEqual((p["next"] or {}).get("id"), picked, f"下見 {p['next']} と配車 {picked} がずれている{extra_msg}\n{out}")
        return p, out

    # ---------- A: 先行票が未完了（#573 / ADR-0078）
    def test_preview_skips_a_ticket_whose_prerequisites_are_not_done(self):
        """先頭の票が先行票を待っているとき、下見は次の回せる票を見せる（dispatch と同じ）"""
        self.new(601); self.new(602, "--depends", "601"); self.new(603)
        self.kb("block", "601", "--note", "人間待ち")
        self.assertEqual(self.kb_next()["id"], 602, "前提の取り違え（kb next は依存を見ない）")

        p, out = self.assertSamePick()
        self.assertEqual(p["next"]["id"], 603)
        self.assertEqual(p["reason"], "picked_next")
        self.assertEqual(p["kb_next"], 602, "薄い kb next が何を返したかも残す（なぜ他を選ばなかったか）")
        self.assertEqual(p["skipped_by_dependency"], {"602": {"601": "blocked"}})
        self.assertEqual(p["skipped_by_pause"], {})
        self.assertIn("602", out)

    def test_preview_says_blocked_by_dependency_when_every_todo_waits(self):
        """todo は在るが全部先行票待ちなら、下見は票を見せず理由を言う（0 件とも読めていないとも別の値）"""
        self.new(611); self.new(612, "--depends", "611")
        self.kb("block", "611", "--note", "人間待ち")

        p, out = self.assertSamePick()
        self.assertIsNone(p["next"], "回せないのに票を見せると、画面が「次はこれ」と読める")
        self.assertEqual(p["reason"], "blocked_by_dependency")
        self.assertEqual(p["skipped_by_dependency"], {"612": {"611": "blocked"}})

    # ---------- B: 解除前の一時停止（ADR-0043 の「下見の表示は別票」が本票）
    def test_preview_skips_a_paused_ticket_before_the_limit_resets(self):
        """利用枠切れで一時停止中（解除前）の票は、dispatch が飛ばす。下見も飛ばす"""
        self.new(931); self.new(932)
        self.paused(931, retry_after="future")
        self.assertEqual(self.kb_next()["id"], 931, "前提の取り違え（kb next は一時停止を見ない）")

        p, out = self.assertSamePick()
        self.assertEqual(p["next"]["id"], 932)
        self.assertEqual(p["reason"], "picked_next")
        self.assertEqual(list(p["skipped_by_pause"]), ["931"])
        self.assertEqual(p["skipped_by_pause"]["931"]["paused"], "quota")
        self.assertTrue(p["skipped_by_pause"]["931"]["until"], "いつ以降なら回るのかを下見が言えない")
        self.assertEqual(p["skipped_by_dependency"], {})
        self.assertIn("931", out)

    def test_preview_says_blocked_by_pause_when_the_only_todo_is_paused(self):
        """一時停止だけで候補が尽きたら、依存とは別の理由コードで言う（新語 blocked_by_pause）"""
        self.new(941)
        self.paused(941, retry_after="future")

        p, out = self.assertSamePick()
        self.assertIsNone(p["next"])
        self.assertEqual(p["reason"], "blocked_by_pause")
        self.assertEqual(list(p["skipped_by_pause"]), ["941"])

    def test_preview_shows_a_paused_ticket_again_once_it_is_ready(self):
        """解除時刻を過ぎた票は dispatch が続きから回す。下見も同じ票を見せる（飛ばし過ぎない）"""
        self.new(951); self.new(952)
        self.paused(951, retry_after="past")

        p, out = self.assertSamePick()
        self.assertEqual(p["next"]["id"], 951)
        self.assertEqual(p["reason"], "picked_next")
        self.assertEqual(p["skipped_by_pause"], {})

    def test_preview_skips_a_ticket_that_hit_the_limit_too_many_times(self):
        """利用枠切れが上限回数続いた票は、解除時刻を過ぎていても自動では回さない（dispatch と同じ）。

        上限を超えた票は普通は kb sync が blocked にする（todo から外れる）。ここでは上限を上げて sync し、
        todo のまま残った票を既定の上限で見せる＝dispatch の「ここでも回さない」の備えと同じ形にする"""
        self.new(961); self.new(962)
        self.paused(961, retry_after="past", hits=9, sync_env={"AIFACTORY_RESUME_MAX_HITS": "99"})

        p, out = self.assertSamePick()
        self.assertEqual(p["next"]["id"], 962)
        self.assertTrue(p["skipped_by_pause"]["961"]["hits_exceeded"])

    def test_preview_skips_a_ticket_waiting_for_a_key(self):
        """鍵が鍵プールに無くて止まった票（ADR-0046）も、鍵が登録されるまでは dispatch が飛ばす"""
        self.new(971); self.new(972)
        self.paused(971, failure="nokey")

        p, out = self.assertSamePick()
        self.assertEqual(p["next"]["id"], 972)
        self.assertEqual(p["skipped_by_pause"]["971"]["paused"], "nokey")
        self.assertEqual(p["skipped_by_pause"]["971"]["needed_keys"], ["fable"])

    # ---------- 変えていないこと
    def test_preview_is_unchanged_on_a_plain_board(self):
        """依存も一時停止も無い板では、今までどおり kb next の 1 件をそのまま見せる"""
        self.new(981); self.new(982)
        p, out = self.assertSamePick()
        self.assertEqual(p["next"]["id"], 981)
        self.assertEqual((p["reason"], p["kb_next"]), ("picked_next", 981))
        self.assertEqual((p["skipped_by_dependency"], p["skipped_by_pause"]), ({}, {}))
        self.assertEqual(p["next"]["status"], "todo")

    def test_preview_has_no_todo_when_the_board_is_empty(self):
        """todo が 1 件も無いのは「確かめた 0 件」。飛ばした結果（blocked_by_*）と混ぜない"""
        p = self.preview(pj="no-such-pj")
        self.assertIsNone(p["next"])
        self.assertEqual(p["reason"], "no_todo")
        self.assertEqual((p["skipped_by_dependency"], p["skipped_by_pause"]), ({}, {}))


if __name__ == "__main__":
    unittest.main()
