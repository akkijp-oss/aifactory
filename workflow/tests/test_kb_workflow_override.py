"""`kb run --workflow X` はチケットの kind（＝何の仕事か）を書き換えない（チケット 249）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。偽 `sandbox` の take を失敗させて短く終える（test_take_failure.py と同じ流儀）。
- kind は据え置き、history と runs/<run>/state.json の workflow に「今回どの workflow で回したか」が残る
- --workflow 無しの --resume は、kind ではなく前回の run の workflow で再開する
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
TICKET = "# 調査: workflow 指定で kind が変わらないこと\n\n偽の sandbox で take を失敗させる。\n"

FAKE = r"""#!/usr/bin/env bash
echo "$@" >> "$CALLS"
case "$1" in
  take) echo "[error] pj=$2 に空きなし" >&2; exit 1 ;;
esac
exit 0
"""


class KbWorkflowOverrideTest(unittest.TestCase):
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
        self.run_name = f"{datetime.date.today().isoformat()}-kumitate-904"
        new = self.kb("new", "kumitate", "bug", "workflow 指定の再現", "--body", "-", "--id", "904", input_text=TICKET)
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)

    def kb(self, *args, input_text=None):
        return subprocess.run([sys.executable, str(KB), *args], input=input_text, text=True, capture_output=True, env=self.env)

    def fields(self):
        show = self.kb("show", "904")
        head = show.stdout.split("-" * 60)[0].splitlines()
        return {l.split(" ", 1)[0]: l.split(" ", 1)[1].strip() for l in head if l.strip()}

    def test_workflow_option_records_the_run_without_changing_kind(self):
        r = self.kb("run", "904", "--workflow", "research")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        t = self.fields()
        self.assertEqual(t["kind"], "bug", t)                       # 種別は「何の仕事か」。実行方法で書き換えない
        self.assertEqual(t["run"], self.run_name, t)
        h = self.kb("history", "904").stdout
        self.assertRegex(h, r"workflow\s+-\s*→\s*research")
        s = json.loads((self.ws / "runs" / self.run_name / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(s["workflow"], "research")

    def test_resume_without_workflow_uses_the_recorded_run_workflow(self):
        self.assertEqual(self.kb("run", "904", "--workflow", "research").returncode, 2)
        r = self.kb("run", "904", "--resume")
        self.assertIn(" research ", [l for l in r.stdout.splitlines() if l.startswith("[kb] ")][0] + " ")


if __name__ == "__main__":
    unittest.main()
