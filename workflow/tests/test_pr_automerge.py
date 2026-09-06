"""条件を満たすときだけ PR を base へマージする automerge step（チケット 358 / ADR-0042）。

  python3 -m unittest discover -s workflow/tests -p 'test_pr_automerge.py' -v

VM も GitHub も使わない。PATH の先頭に偽の `sandbox`（ssh はその場で bash 実行）と偽の `gh`（呼び出しを
calls.log に足し、応答は env で変える）を置いて kit/steps/pr-automerge.sh をそのまま回す（test_take_failure.py と同じ流儀）。

- 全部緑 → gh pr merge と gh pr comment が呼ばれ、merged.json が残り、最終行は MERGED
- CI 赤 / draft / review FAIL / gates FAIL / 宛先ちがい → マージを呼ばず、最終行に理由が NOMERGE で出て rc=1
- checks が pending のまま上限 → NOMERGE。checks が 0 本は require_checks 次第
"""
import json
import os
import pathlib
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "workflow" / "kit" / "steps" / "pr-automerge.sh"
BRANCH = "sandbox/358-feature-auto-merge"

FAKE_SANDBOX = r"""#!/usr/bin/env bash
case "$1" in
  ssh) shift 2; exec bash -c "$1" ;;
esac
exit 0
"""

# gh の代わり。--json に何を頼まれたかで応答を変える（gh の -q は解釈せず、完成した 1 行を返す）
FAKE_GH = r"""#!/usr/bin/env bash
echo "$*" >> "$CALLS"
case "$*" in
  *"--json state,isDraft"*) echo "${GH_VIEW}" ;;
  *"--json name,state,bucket"*)
    if [ -n "${GH_CHECKS}" ]; then printf '%s\n' "${GH_CHECKS}"; exit 1; fi ;;
  *"--json mergeable"*) echo "${GH_MERGEABLE}" ;;
  *"--json state,url"*) echo "${GH_AFTER}" ;;
  *"--json mergeCommit"*) echo "${GH_SHA}" ;;
  "pr merge"*)
    if [ -n "${GH_MERGE_FAILS}" ]; then echo "fatal: base branch was modified" >&2; exit 1; fi
    echo "Merged pull request #1" ;;
esac
exit 0
"""

GATES_GREEN = "PASS unittest-workflow\nINFO unittest-console red (also red on base; not a gate)\n=== base check: ...\nFAIL この行はログの中なので見ない\n"
GATES_RED = "PASS unittest-workflow\nFAIL oss-check\n=== base check: ...\n"


class PrAutomergeTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.bin = self.ws / "bin"; self.bin.mkdir()
        for name, body in (("sandbox", FAKE_SANDBOX), ("gh", FAKE_GH)):
            f = self.bin / name; f.write_text(body); f.chmod(0o755)
        self.app = self.ws / "app"; self.app.mkdir()
        self.work = self.ws / "work"; self.work.mkdir()
        self.calls = self.ws / "calls.log"; self.calls.write_text("")
        (self.work / "pr_url").write_text("https://github.com/akkijp-oss/aifactory/pull/1\n", encoding="utf-8")
        (self.work / "gates.txt").write_text(GATES_GREEN, encoding="utf-8")
        (self.work / "review.md").write_text("# レビュー: PASS\n\n判定の理由。\n", encoding="utf-8")

    def run_step(self, **env):
        e = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", SANDBOX_APP_DIR=str(self.app), CALLS=str(self.calls),
                 TASK="358", WORK=str(self.work), BASE="develop", BRANCH=BRANCH, RUN_NAME="2026-09-09-aifactory-358",
                 HAS_REVIEW="1", AUTO_MERGE_METHOD="merge", AUTO_MERGE_WAIT_MIN="20",
                 AUTO_MERGE_DELETE_BRANCH="0", AUTO_MERGE_REQUIRE_CHECKS="1",
                 AUTOMERGE_POLL_S="0", AUTOMERGE_ZERO_CHECKS_GRACE_S="0", AUTOMERGE_MERGEABLE_POLL_S="0",
                 GH_VIEW=f"OPEN false develop {BRANCH}", GH_CHECKS="pass ci / test\npass ci / docs",
                 GH_MERGEABLE="MERGEABLE", GH_AFTER="MERGED https://github.com/akkijp-oss/aifactory/pull/1", GH_SHA="abc1234",
                 GH_MERGE_FAILS="")
        e.update({k: str(v) for k, v in env.items()})
        return subprocess.run(["bash", str(SCRIPT)], text=True, capture_output=True, env=e)

    def last_line(self, p):
        return [l for l in p.stdout.splitlines() if l.strip()][-1]

    def gh_calls(self):
        return self.calls.read_text(encoding="utf-8")

    def assert_no_merge(self, p, why):
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(self.last_line(p), f"NOMERGE: {why}", p.stdout)
        self.assertNotIn("pr merge", self.gh_calls())
        self.assertFalse((self.work / "merged.json").exists())

    # ---------- guest の中で走らせる経路（pull worker。チケット 386）
    def test_sb_local_runs_without_the_sandbox_cli(self):
        """pull worker には `sandbox ssh` が無いので、backend はこの script を guest に置いて guest の中で走らせる。
        SB_LOCAL=1 のとき `sandbox` を 1 度も呼ばずに、緑の一式がマージまで通ることを固定する"""
        (self.bin / "sandbox").unlink()                       # PATH から偽 sandbox を外す（本物も無い）
        p = self.run_step(SB_LOCAL="1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("sandbox", p.stderr)
        self.assertIn("pr merge 1 --merge", self.gh_calls())
        self.assertEqual(self.last_line(p), "MERGED: abc1234 https://github.com/akkijp-oss/aifactory/pull/1")
        self.assertTrue((self.work / "merged.json").exists())

    def test_without_sb_local_the_sandbox_cli_is_still_used(self):
        """Proxmox backend の経路（制御系から VM に入る）は変えない"""
        loud = FAKE_SANDBOX.replace('case "$1" in', 'echo "sandbox $*" >> "$CALLS"\ncase "$1" in', 1)
        f = self.bin / "sandbox"; f.write_text(loud); f.chmod(0o755)
        p = self.run_step()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("sandbox ssh", self.gh_calls())

    # ---------- 全部緑
    def test_merges_and_records_when_every_condition_is_met(self):
        p = self.run_step()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("pr merge 1 --merge", self.gh_calls())
        self.assertIn("pr comment 1", self.gh_calls())
        self.assertEqual(self.last_line(p), "MERGED: abc1234 https://github.com/akkijp-oss/aifactory/pull/1")
        m = json.loads((self.work / "merged.json").read_text(encoding="utf-8"))
        self.assertEqual((m["sha"], m["method"], m["base"]), ("abc1234", "merge", "develop"))
        self.assertTrue(m["at"])
        self.assertIn("checks 2 本 pass", self.gh_calls())

    def test_merge_method_and_branch_deletion_follow_the_project_setting(self):
        p = self.run_step(AUTO_MERGE_METHOD="squash", AUTO_MERGE_DELETE_BRANCH="1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("pr merge 1 --squash", self.gh_calls())
        self.assertEqual(json.loads((self.work / "merged.json").read_text(encoding="utf-8"))["method"], "squash")

    # ---------- 条件を満たさない（PR は開いたまま）
    def test_red_ci_stops_before_merging(self):
        p = self.run_step(GH_CHECKS="pass ci / docs\nfail ci / test")
        self.assert_no_merge(p, "CI 赤 (ci / test)")

    def test_a_draft_pr_is_not_merged(self):
        p = self.run_step(GH_VIEW=f"OPEN true develop {BRANCH}")
        self.assert_no_merge(p, "PR #1 が draft（ゲートに FAIL があったときの印）")

    def test_a_pr_pointing_at_another_base_is_not_merged(self):
        p = self.run_step(GH_VIEW=f"OPEN false main {BRANCH}")
        self.assert_no_merge(p, "PR #1 の宛先が develop でない (main)")

    def test_a_failed_review_stops_before_merging(self):
        (self.work / "review.md").write_text("# レビュー: FAIL\n\n直すところ。\n", encoding="utf-8")
        p = self.run_step()
        self.assert_no_merge(p, "review が PASS でない (# レビュー: FAIL)")

    def test_review_is_not_required_when_the_workflow_has_no_reviewer(self):
        (self.work / "review.md").unlink()
        p = self.run_step(HAS_REVIEW="0")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("pr merge 1 --merge", self.gh_calls())

    def test_red_gates_stop_before_merging(self):
        (self.work / "gates.txt").write_text(GATES_RED, encoding="utf-8")
        p = self.run_step()
        self.assert_no_merge(p, "gates 赤 (oss-check)")

    def test_a_pr_that_is_not_mergeable_stops_before_merging(self):
        p = self.run_step(GH_MERGEABLE="CONFLICTING")
        self.assert_no_merge(p, "PR #1 が MERGEABLE でない (CONFLICTING)")

    def test_a_missing_pr_url_stops_before_merging(self):
        (self.work / "pr_url").unlink()
        p = self.run_step()
        self.assert_no_merge(p, "pr_url が無いので PR を特定できない")

    # ---------- checks の待ち
    def test_checks_that_never_finish_stop_at_the_time_limit(self):
        p = self.run_step(GH_CHECKS="pending ci / test", AUTO_MERGE_WAIT_MIN="1", AUTOMERGE_WAIT_S="0")
        self.assert_no_merge(p, "checks が 1 分で終わらない (ci / test)")

    def test_an_unknown_bucket_is_treated_as_pending_and_never_merges_on_its_own(self):
        """gh の版で bucket の語彙が増えても、知らない値を勝手に合格にしない"""
        p = self.run_step(GH_CHECKS="queued ci / test", AUTO_MERGE_WAIT_MIN="1", AUTOMERGE_WAIT_S="0")
        self.assert_no_merge(p, "checks が 1 分で終わらない (ci / test)")

    def test_zero_checks_stop_when_the_project_requires_checks(self):
        p = self.run_step(GH_CHECKS="")
        self.assert_no_merge(p, "checks が 0 本（CI が動いていない）")

    def test_zero_checks_are_allowed_when_the_project_says_so(self):
        p = self.run_step(GH_CHECKS="", AUTO_MERGE_REQUIRE_CHECKS="0")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("pr merge 1 --merge", self.gh_calls())
        self.assertIn("checks 0 本 pass", self.gh_calls())

    def test_a_merge_commit_that_is_not_visible_yet_still_records_the_merge(self):
        """マージ直後は mergeCommit が null で返ることがある。sha が空でも「マージした」事実は残す"""
        p = self.run_step(GH_SHA="null")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        m = json.loads((self.work / "merged.json").read_text(encoding="utf-8"))
        self.assertEqual(m["sha"], "")
        self.assertTrue(m["at"] and m["pr_url"])

    # ---------- マージ自体が失敗した
    def test_a_failing_gh_pr_merge_is_reported_on_the_last_line(self):
        p = self.run_step(GH_MERGE_FAILS="1")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(self.last_line(p), "NOMERGE: gh pr merge が失敗: fatal: base branch was modified")
        self.assertFalse((self.work / "merged.json").exists())

    def test_a_pr_that_is_still_open_after_the_merge_call_is_not_recorded_as_merged(self):
        p = self.run_step(GH_AFTER="OPEN https://github.com/akkijp-oss/aifactory/pull/1", GH_SHA="null")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(self.last_line(p), "NOMERGE: gh pr merge の後も PR #1 が MERGED になっていない (OPEN)")
        self.assertFalse((self.work / "merged.json").exists())


if __name__ == "__main__":
    unittest.main()
