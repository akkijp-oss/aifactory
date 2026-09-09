"""人間の後始末（wip から PR を作ってマージ・打ち切り）を kb が run の記録に転記する（チケット 335）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。runner（workflow/bin/run）は起動せず、human で止まった state.json を手で置いて kb を呼ぶ。
- `kb run-note <run> --result done --pr N` が state.json に `human` を足す（runner が書いた `result` は変えない）
- 2 回目は断る（`--force` でだけ書き足す）。実行中（finished が無い）の run にも書かない
- `kb set <id> --pr N` / `kb done <id>` は、紐づく run が human なら自動で転記する
- runner 由来の `kb sync` は転記しない（runner が付けた PR を「人間が仕上げた」にしない）
- 同じチケットを回し直すと、チケットのメモが「再走中（attempt N・workflow W）」に替わる
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
TICKET = "# 機能: 人間の後始末を run に残す\n\n偽の sandbox で take を失敗させる。\n"

# take を失敗させて短く終える（test_kb_run_from.py と同じ流儀。runner の中には入らない）
FAKE = r"""#!/usr/bin/env bash
case "$1" in
  take) echo "[error] pj=$2 に空きなし" >&2; exit 1 ;;
esac
exit 0
"""


class KbRunNoteTest(unittest.TestCase):
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
        self.today = f"{datetime.date.today().isoformat()}-kumitate-906"
        self.prev = "2026-09-06-kumitate-906"
        new = self.kb("new", "kumitate", "feature", "後始末を残す", "--body", "-", "--id", "906", input_text=TICKET)
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)

    def kb(self, *args, input_text=None):
        return subprocess.run([sys.executable, str(KB), *args], input=input_text, text=True, capture_output=True, env=self.env)

    def stopped_run(self, name, link=True, **kw):
        """human で止まった run の記録（runner が残す形）を置き、チケットの run 欄をそれに向ける"""
        d = self.ws / "runs" / name; d.mkdir(parents=True, exist_ok=True)
        state = {"pj": "kumitate", "task": "906", "workflow": "feature", "branch": "sandbox/906-feature-x", "base": "main",
                 "result": "human", "next": "human", "pr_url": "", "wip_branch": "sandbox/906-feature-wip",
                 "resume_step": "implement", "error": "review で止まった（失敗、または戻せる回数を使い切った）: 指摘 1 件",
                 "started": "2026-09-06T09:00:00+09:00", "finished": "2026-09-06T10:00:00+09:00", "elapsed_s": 3600,
                 "loops": {"review->implement": 1}, "history": [{"step": "review", "ok": False, "next": "human", "at": "2026-09-06T10:00:00+09:00"}]}
        state.update(kw)
        for k, v in list(state.items()):
            if v is None: del state[k]
        (d / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        if link: self.assertEqual(self.kb("set", "906", "--run", name).returncode, 0)
        return name

    def state(self, name):
        return json.loads((self.ws / "runs" / name / "state.json").read_text(encoding="utf-8"))

    def note(self):
        """kb show の note 欄。run 由来の行を足した後は複数行になる（342）ので、次の欄が来るまでを note とする"""
        lines = self.kb("show", "906").stdout.splitlines()
        i = next((i for i, l in enumerate(lines) if l.startswith("note ")), None)
        if i is None: return ""
        out = [lines[i].split(" ", 1)[1].strip()]
        for l in lines[i + 1:]:
            if l.startswith(("created ", "updated ", "-" * 10)): break
            out.append(l)
        return "\n".join(out).strip()

    def test_run_note_records_the_human_closeout_without_touching_the_runner_result(self):
        """人間が PR を作ってマージしたことを run 記録に足す。runner が確定した result は動かさない"""
        self.stopped_run(self.prev)
        r = self.kb("run-note", self.prev, "--result", "done", "--pr", "300", "--text", "wip から PR を作ってマージした")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = self.state(self.prev)
        self.assertEqual(s["result"], "human", "runner が書いた result を上書きしている")
        h = s["human"]
        self.assertEqual(h["result"], "done"); self.assertEqual(h["by"], "pm")
        self.assertEqual(h["text"], "wip から PR を作ってマージした")
        self.assertEqual(h["pr_url"], "https://github.com/akkijp/kumitate/pull/300")   # PJ 定義の repo から組む
        self.assertRegex(h["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")    # 記録する時刻はオフセット付き（ADR-0026）
        self.assertEqual(s["history"], [{"step": "review", "ok": False, "next": "human", "at": "2026-09-06T10:00:00+09:00"}])

    def test_a_second_run_note_is_refused_unless_forced(self):
        """人が 2 人続けて書くと後の方だけが残る。上書きは --force を要る形にして事故を防ぐ"""
        self.stopped_run(self.prev)
        self.assertEqual(self.kb("run-note", self.prev, "--pr", "300").returncode, 0)
        r = self.kb("run-note", self.prev, "--pr", "301")
        self.assertEqual(r.returncode, 1); self.assertIn("既に人間の記録がある", r.stderr)
        self.assertIn("/pull/300", self.state(self.prev)["human"]["pr_url"])
        f = self.kb("run-note", self.prev, "--text", "説明を足す", "--force")
        self.assertEqual(f.returncode, 0, f.stdout + f.stderr)
        h = self.state(self.prev)["human"]
        self.assertEqual(h["text"], "説明を足す")
        self.assertEqual(h["result"], "done"); self.assertIn("/pull/300", h["pr_url"])   # 省いた項目は前のまま

    def test_a_running_run_is_not_written(self):
        """runner が state.json を書いている最中（finished が無い）の run には書かない"""
        self.stopped_run(self.prev, finished=None, result=None)
        r = self.kb("run-note", self.prev, "--pr", "300")
        self.assertEqual(r.returncode, 1); self.assertIn("まだ実行中", r.stderr)
        self.assertNotIn("human", self.state(self.prev))
        r = self.kb("run-note", "2020-01-01-kumitate-999", "--pr", "1")
        self.assertEqual(r.returncode, 1); self.assertIn("実行記録が無い", r.stderr)

    def test_setting_the_pr_transcribes_to_the_linked_human_run(self):
        """`kb set <id> --pr N`（console のチケット画面・MCP の ticket_action set と同じ道）で run にも転記される"""
        self.stopped_run(self.prev)
        r = self.kb("set", "906", "--pr", "300")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        h = self.state(self.prev)["human"]
        self.assertEqual(h["result"], "done"); self.assertIn("/pull/300", h["pr_url"]); self.assertIn("906", h["text"])
        self.assertEqual(self.state(self.prev)["result"], "human")

    def test_done_transcribes_and_does_not_overwrite_an_existing_record(self):
        """`kb done` でも転記する。既に人間の記録があれば触らない（先に書いた説明を消さない）"""
        self.stopped_run(self.prev)
        self.assertEqual(self.kb("done", "906").returncode, 0)
        self.assertEqual(self.state(self.prev)["human"]["result"], "done")

        other = self.stopped_run("2026-09-05-kumitate-906", human={"at": "2026-09-06T12:00:00+09:00", "by": "pm",
                                                                  "result": "abandoned", "pr_url": "", "text": "作り直す"})
        self.assertEqual(self.kb("set", "906", "--pr", "301").returncode, 0)
        h = self.state(other)["human"]
        self.assertEqual(h["result"], "abandoned"); self.assertEqual(h["text"], "作り直す")

    def test_the_runner_s_own_pr_is_not_recorded_as_a_human_closeout(self):
        """kb sync（runner の記録からチケットを進める道）は転記しない。runner が作った PR を「人間が仕上げた」にしない"""
        name = self.stopped_run(self.prev, result="end", pr_url="https://github.com/akkijp/kumitate/pull/77 MERGED", next="end")
        r = self.kb("sync", "906")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("human", self.state(name))
        self.assertIn("done", self.kb("show", "906").stdout)

    def test_rerunning_replaces_the_stale_human_note_on_the_ticket(self):
        """同じ名前の run をやり直すと、チケットのメモが前回の失敗文から「再走中」に替わる（#294）"""
        self.stopped_run(self.prev)
        self.assertEqual(self.kb("sync", "906").returncode, 0)
        self.assertIn("人間へ", self.note())
        (self.ws / "runs" / self.today).mkdir(parents=True)          # 今日すでに 1 回走っている（runner が -attempt1 に退避する）
        r = self.kb("run", "906")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)       # take が失敗して終わる（VM は使わない）
        self.assertIn("再走中（attempt 1・workflow feature）", self.kb("history", "906").stdout)

    def test_restarting_from_a_step_names_the_previous_run_in_the_note(self):
        """`--from` の再走（新しい VM で前回の続き）も「再走中」。どの run のどこから続けているかをメモに残す"""
        self.stopped_run(self.prev)
        self.assertEqual(self.kb("sync", "906").returncode, 0)
        r = self.kb("run", "906", "--from", "implement", "--branch", "sandbox/906-feature-wip")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        # take が失敗した後のメモは「VM を取得できず…」に進むので、置き換わったことは history で見る
        hist = self.kb("history", "906").stdout
        self.assertIn("再走中（workflow feature）", hist)             # 今日の run はまだ無いので attempt は付けない
        self.assertIn(f"前回: {self.prev} の implement から", hist)
        self.assertNotIn("人間へ", self.note())

    def test_a_first_run_leaves_the_note_alone(self):
        """初回の run はメモを触らない（今までどおり。空のメモで上書きしない）。
           run が終わった後も人の文は消えない（342。history だけ見ていると上書きに気づけないので note も見る）"""
        self.assertEqual(self.kb("set", "906", "--note", "PM: 急ぎ").returncode, 0)
        r = self.kb("run", "906")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("PM: 急ぎ", self.kb("history", "906").stdout)
        self.assertIn("PM: 急ぎ", self.note())


if __name__ == "__main__":
    unittest.main()
