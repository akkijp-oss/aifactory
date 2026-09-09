"""run 由来の状態文が、人（PM）が kb set --note で書いた申し送りを消さないこと（チケット 342）。

  python3 -m unittest workflow.tests.test_kb_run_keeps_note -v

VM も claude も使わない。偽の sandbox で take を失敗させ、human で止まった state.json は手で置く
（test_kb_run_note.py と同じ流儀）。見るのはチケットの note だけ:
- note の 1 行目が `[run] ` で始まる行 = 機械（run / sync）が書き換える状態の要約
- 2 行目以降 = 人のもの。run / sync / 再走のどれを通っても消えない
- 旧形式（`[run] ` が無い run 由来の文言）が 1 行目にある既存チケットは、次の run で捨てる（移行不要）
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
TICKET = "# 修正: PM の申し送りを消さない\n\n偽の sandbox で take を失敗させる。\n"
PM = "Mac (Claude Code MBP) で実施。Linux sandbox は #250 未マージで gates 赤のため"

FAKE = r"""#!/usr/bin/env bash
case "$1" in
  take) echo "[error] pj=$2 に空きなし" >&2; exit 1 ;;
esac
exit 0
"""


class KbRunKeepsNoteTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.bin = self.ws / "bin"; self.bin.mkdir()
        fake = self.bin / "sandbox"; fake.write_text(FAKE); fake.chmod(0o755)
        (self.ws / "sandbox-state.json").write_text("{}")
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        AIFACTORY_WORKSPACE=str(self.ws), SANDBOX_STATE=str(self.ws / "sandbox-state.json"),
                        AIFACTORY_ACTOR="pm")
        self.env.pop("AIFACTORY_FROM_RUN", None)
        self.today = f"{datetime.date.today().isoformat()}-kumitate-907"
        self.prev = "2026-09-06-kumitate-907"
        new = self.kb("new", "kumitate", "bug", "申し送りを消さない", "--body", "-", "--id", "907", input_text=TICKET)
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)

    def kb(self, *args, input_text=None):
        return subprocess.run([sys.executable, str(KB), *args], input=input_text, text=True, capture_output=True, env=self.env)

    def stopped_run(self, name, link=True, **kw):
        """human で止まった run の記録（runner が残す形）を置き、チケットの run 欄をそれに向ける"""
        d = self.ws / "runs" / name; d.mkdir(parents=True, exist_ok=True)
        state = {"pj": "kumitate", "task": "907", "workflow": "bug", "branch": "sandbox/907-bug-x", "base": "main",
                 "result": "human", "next": "human", "pr_url": "", "wip_branch": "sandbox/907-bug-wip",
                 "resume_step": "implement", "error": "review で止まった: 指摘 1 件",
                 "started": "2026-09-06T09:00:00+09:00", "finished": "2026-09-06T10:00:00+09:00", "elapsed_s": 3600}
        state.update(kw)
        for k, v in list(state.items()):
            if v is None: del state[k]
        (d / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        if link: self.assertEqual(self.kb("set", "907", "--run", name).returncode, 0)
        return name

    def note(self):
        """kb show の note 欄（複数行）。show は 1 行 1 項目で出すので、次の欄が来るまでを note とする"""
        lines = self.kb("show", "907").stdout.splitlines()
        i = next((i for i, l in enumerate(lines) if l.startswith("note ")), None)
        if i is None: return ""
        out = [lines[i].split(" ", 1)[1].strip()]
        for l in lines[i + 1:]:
            if l.startswith(("created ", "updated ", "-" * 10)): break
            out.append(l)
        return "\n".join(out).strip()

    def set_pm_note(self):
        self.assertEqual(self.kb("set", "907", "--note", PM).returncode, 0)

    def test_a_failed_run_keeps_the_pm_note_under_its_own_line(self):
        """run が VM を取れずに落ちても、PM の申し送りは残る（1 行目だけが run のもの）"""
        self.set_pm_note()
        r = self.kb("run", "907")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        note = self.note()
        self.assertTrue(note.splitlines()[0].startswith("[run] VM を取得できず"), note)
        self.assertIn(PM, "\n".join(note.splitlines()[1:]), note)

    def test_sync_replaces_only_the_run_line(self):
        """kb sync を 2 回通しても run 行だけが入れ替わり、PM の申し送りは 1 つだけ残る（重ならない）"""
        self.set_pm_note()
        self.stopped_run(self.prev)
        self.assertEqual(self.kb("sync", "907").returncode, 0)
        note = self.note()
        self.assertTrue(note.splitlines()[0].startswith("[run] 人間へ"), note)
        self.assertIn(PM, note)

        self.stopped_run(self.prev, result="end", pr_url="https://github.com/akkijp/kumitate/pull/77", error=None)
        self.assertEqual(self.kb("sync", "907").returncode, 0)
        note = self.note()
        self.assertTrue(note.splitlines()[0].startswith("[run] PR 待ち "), note)
        self.assertNotIn("人間へ", note)
        self.assertEqual(note.count(PM), 1, note)

    def test_rerunning_keeps_the_pm_note_through_the_rerun_line(self):
        """再走（--from）でメモを「再走中」に替えるときも、PM の申し送りは残る"""
        self.set_pm_note()
        self.stopped_run(self.prev)
        self.assertEqual(self.kb("sync", "907").returncode, 0)
        r = self.kb("run", "907", "--from", "implement", "--branch", "sandbox/907-bug-wip")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        hist = self.kb("history", "907").stdout
        self.assertIn("[run] 再走中（workflow bug）", hist)
        note = self.note()
        self.assertTrue(note.splitlines()[0].startswith("[run] VM を取得できず"), note)
        self.assertIn(PM, note)

    def test_a_legacy_run_line_is_dropped_instead_of_kept_as_a_human_line(self):
        """`[run] ` が付く前の run 由来の文言（既存チケット）は、次の run で人の行に格上げせず捨てる（移行不要）"""
        self.assertEqual(self.kb("set", "907", "--note", "人間へ（wip: origin/sandbox/907-bug-wip）: 指摘 1 件").returncode, 0)
        self.stopped_run(self.prev, result="end", pr_url="https://github.com/akkijp/kumitate/pull/77", error=None)
        self.assertEqual(self.kb("sync", "907").returncode, 0)
        note = self.note()
        self.assertTrue(note.splitlines()[0].startswith("[run] PR 待ち "), note)
        self.assertNotIn("人間へ", note)
        self.assertEqual(len(note.splitlines()), 1, note)

    def test_a_person_still_owns_the_note(self):
        """人の kb set --note は今までどおり丸ごと書く（run 行も消える）。--note '' で空に戻せる"""
        self.set_pm_note()
        self.stopped_run(self.prev)
        self.assertEqual(self.kb("sync", "907").returncode, 0)
        self.assertIn("[run] ", self.note())
        self.assertEqual(self.kb("set", "907", "--note", "PM: 別の申し送り").returncode, 0)
        self.assertEqual(self.note(), "PM: 別の申し送り")
        self.assertEqual(self.kb("set", "907", "--note", "").returncode, 0)
        self.assertEqual(self.note(), "")

    def test_the_dry_run_preview_shows_the_combined_note(self):
        """kb sync --dry-run の after.note（console の下見が読む）も合成後の文にする"""
        self.set_pm_note()
        self.stopped_run(self.prev)
        r = self.kb("sync", "907", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(d["after"]["note"].splitlines()[0].startswith("[run] 人間へ"), d)
        self.assertIn(PM, d["after"]["note"])
        self.assertEqual(self.note(), PM, "dry-run が DB を書いている")


if __name__ == "__main__":
    unittest.main()
