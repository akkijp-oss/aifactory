"""制御系の gh が GH_TOKEN 無しでも GitHub App から払い出して動くこと（チケット 249）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。PATH の先頭に偽の `sandbox` と `gh` を置いて runner を回す（test_take_failure.py と同じ流儀）。
- GH_TOKEN が無い → `sandbox gh-app token <pj>` で補い、`gh pr view` が通る
- GH_TOKEN が既にある → App を呼ばない
- どちらも無い → gh の理由と App の理由の両方を添えて落ちる
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
RUNNER = REPO / "workflow" / "bin" / "run"
TOKEN = "ghs_fake_token_value"
TICKET = "# 調査: GH_TOKEN 補完の再現\npr: 20\n\n偽の gh / sandbox で制御系のトークン払い出しを見る。\n"

# GH_TOKEN が空なら gh auth login を促して落ちる本物と同じ振る舞い（値は calls.log に残さない）
FAKE_GH = r"""#!/usr/bin/env bash
if [ -n "$GH_TOKEN" ]; then st=set; else st=empty; fi
echo "gh $* token=$st" >> "$CALLS"
if [ "$st" = empty ]; then
  echo "To get started with GitHub CLI, please run:  gh auth login" >&2
  exit 4
fi
echo '{"headRefName":"sandbox/20-x","baseRefName":"main","state":"OPEN"}'
"""

FAKE_SANDBOX = r"""#!/usr/bin/env bash
echo "$@" >> "$CALLS"
case "$1" in
  gh-app)
    if [ "$2" = token ]; then
      if [ -n "$APP_FAILS" ]; then echo "[error] GitHub App が install されていない" >&2; exit 1; fi
      echo "TOKEN_VALUE"
    fi
    ;;
  take)
    if [ -n "$TAKE_FAILS" ]; then echo "[error] pj=$2 に空きなし" >&2; exit 1; fi
    echo "take: sb-t-$2-01 10.77.1.1"
    ;;
esac
exit 0
""".replace("TOKEN_VALUE", TOKEN)


class GhTokenFromAppTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.bin = self.ws / "bin"; self.bin.mkdir()
        for name, body in (("sandbox", FAKE_SANDBOX), ("gh", FAKE_GH)):
            f = self.bin / name; f.write_text(body); f.chmod(0o755)
        (self.ws / "sandbox-state.json").write_text("{}")
        self.ticket = self.ws / "ticket.md"; self.ticket.write_text(TICKET, encoding="utf-8")
        self.calls = self.ws / "calls.log"

    def run_runner(self, task="920", **extra):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                   AIFACTORY_WORKSPACE=str(self.ws), SANDBOX_STATE=str(self.ws / "sandbox-state.json"),
                   CALLS=str(self.calls), TAKE_FAILS="1", APP_FAILS="")
        env.pop("GH_TOKEN", None)               # 開発機に本物があっても再現するように
        env.update(extra)
        p = subprocess.run([sys.executable, str(RUNNER), "kumitate", task, "merge-pr", str(self.ticket)],
                           text=True, capture_output=True, env=env)
        import datetime
        return p, self.ws / "runs" / f"{datetime.date.today().isoformat()}-kumitate-{task}"

    def calls_text(self):
        return self.calls.read_text(encoding="utf-8") if self.calls.exists() else ""

    def test_missing_gh_token_is_filled_from_the_github_app(self):
        p, run_dir = self.run_runner()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)     # take で止まる（gh は通っている）
        s = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual((s["branch"], s["base"]), ("sandbox/20-x", "main"))
        self.assertIn("gh-app token kumitate", self.calls_text())
        self.assertIn("token=set", self.calls_text())
        # トークンの値はどこにも出さない
        for where, text in (("calls.log", self.calls_text()), ("state.json", (run_dir / "state.json").read_text(encoding="utf-8")),
                            ("stdout", p.stdout), ("stderr", p.stderr)):
            self.assertNotIn(TOKEN, text, where)

    def test_existing_gh_token_is_used_as_is(self):
        p, run_dir = self.run_runner(task="921", GH_TOKEN="static")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertNotIn("gh-app token", self.calls_text())
        self.assertIn("token=set", self.calls_text())

    def test_no_token_and_no_app_reports_both_reasons(self):
        p, run_dir = self.run_runner(task="922", APP_FAILS="1")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("gh auth login", p.stderr)
        self.assertIn("sandbox gh-app token kumitate", p.stderr)


if __name__ == "__main__":
    unittest.main()
