"""配車（dispatch）も先行条件（depends_on）を見る（チケット 573 / ADR-0078）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。偽 `sandbox` の take を失敗させて `kb run` の先へ行かせない（test_kb_resume_paused.py と同じ流儀）。
「どの票を選んだか / 何を理由に飛ばしたか」は dispatch の標準出力・logs/dispatch.log・偽 sandbox の呼ばれ方だけで確かめられる。

- 未完了の先行票を持つ票は dispatch の対象から外れ、次の票が回る
- 飛ばしたときは理由（票 id と 先行票 id=status）が標準出力と dispatch.log の両方に出る（黙って外さない）
- 先行票が done なら今までどおり回る。DB に無い先行票は「未起票」＝未完了に倒す
- `depends_on` を持たない票の挙動は変わらない
- 判定は core の 1 か所にしかない（dispatch 側に規則の写しが無いことを機械で検査する）
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
KB = REPO / "kanban" / "bin" / "kb"
DISPATCH = REPO / "glue" / "bin" / "dispatch"
TICKET = "# バグ: 先行条件の確認\n\n偽の sandbox で take を失敗させる。\n"

FAKE = r"""#!/usr/bin/env bash
echo "$@" >> "$CALLS"
case "$1" in
  take) echo "[error] pj=$2 に空きなし" >&2; exit 1 ;;
esac
exit 0
"""


class DispatchDependsOnTest(unittest.TestCase):
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

    def kb(self, *args, input_text=None):
        return subprocess.run([sys.executable, str(KB), *args], input=input_text, text=True, capture_output=True, env=self.env)

    def new(self, tid, *extra, title="先行条件の確認"):
        r = self.kb("new", "kumitate", "bug", title, "--body", "-", "--id", str(tid), *extra, input_text=TICKET)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def dispatch(self, *args):
        r = subprocess.run([sys.executable, str(DISPATCH), *args], text=True, capture_output=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def log_text(self):
        f = self.ws / "logs" / "dispatch.log"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def calls_text(self):
        return self.calls.read_text(encoding="utf-8") if self.calls.exists() else ""

    def test_a_ticket_with_an_unmet_prerequisite_is_skipped_and_the_next_one_runs(self):
        self.new(940, "--depends", "941")
        self.new(941)
        out = self.dispatch("--once").stdout
        self.assertIn("940", out); self.assertIn("先行票 941", out); self.assertIn("飛ばす", out)
        self.assertIn("start 941", out)                        # 依存の無い次の票がその回で回る
        # 理由は標準出力だけでなく記録にも残る（黙って対象外にしない）
        self.assertRegex(self.log_text(), r"940 kumitate: 先行票 941=todo が未完了 → 飛ばす")
        # runner に渡ったのは 941 だけ。940 は VM を取りに行っていない
        self.assertIn("take kumitate 941", self.calls_text())
        self.assertNotIn("940", self.calls_text())

    def test_a_done_prerequisite_lets_the_ticket_run_as_before(self):
        self.new(939)
        self.new(940, "--depends", "939")
        self.assertEqual(self.kb("done", "939").returncode, 0)
        out = self.dispatch("--once").stdout
        self.assertIn("start 940", out)
        self.assertNotIn("先行票", out)
        self.assertIn("take kumitate 940", self.calls_text())

    def test_a_prerequisite_missing_from_the_board_counts_as_unmet(self):
        self.new(940, "--depends", "999")
        out = self.dispatch("--once").stdout
        self.assertIn("先行票 999=未起票 が未完了 → 飛ばす", out)
        self.assertNotIn("start 940", out)

    def test_when_every_todo_waits_on_a_prerequisite_the_runner_is_never_called(self):
        self.new(940, "--depends", "999")
        self.new(942, "--depends", "999")
        out = self.dispatch("--once").stdout
        self.assertIn("todo が無い（または全部飛ばした）。終了", out)
        self.assertEqual(out.count("が未完了 → 飛ばす"), 2)      # 2 件とも理由が出る
        self.assertFalse(self.calls.exists(), self.calls_text())

    def test_a_ticket_without_depends_on_behaves_exactly_as_before(self):
        self.new(940)
        out = self.dispatch("--once").stdout
        self.assertIn("start 940", out)
        self.assertNotIn("先行票", out)
        self.assertIn("take kumitate 940", self.calls_text())

    def test_the_rule_is_not_copied_into_dispatch(self):
        """完了条件「判定が core の 1 か所だけにある」の機械検査（ADR-0015 / ADR-0078）"""
        src = DISPATCH.read_text(encoding="utf-8")
        self.assertIn("core.pm_unmet_deps(", src)              # 判定は core を呼ぶ 1 行だけ
        for copied in ('"done"', "'done'", "re.findall", "import re"):
            self.assertNotIn(copied, src, f"先行条件の規則が dispatch に写っている: {copied}")


if __name__ == "__main__":
    unittest.main()
