"""changelog.d/ を CHANGELOG.md に集約する bin/changelog-release（チケット 239）。

  python3 -m unittest discover -s workflow/tests -v

VM も GitHub も使わない。一時 dir に偽の checkout（CHANGELOG.md + changelog.d/）を作って回す。
- 空なら CHANGELOG.md を触らず 0
- 2 ファイルをファイル名の昇順で見出しごとに振り分け、元のファイルは消える
- Unreleased に無い見出しは Keep a Changelog の並びで作る
- check は「CHANGELOG.md の直接編集 → 1」「集約（changelog.d の削除つき）→ 0」「差分なし → 0」
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "bin" / "changelog-release"

CHANGELOG = """# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added
- 既にある項目

### Fixed
- 既にある修正

## [0.3.0] - 2026-09-08

### Added
- 前の版
"""


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if p.returncode: raise AssertionError(f"git {' '.join(args)}: {p.stdout}{p.stderr}")
    return p.stdout


class ChangelogReleaseTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.root = pathlib.Path(d.name)
        (self.root / "bin").mkdir()
        # スクリプトは自分の 2 つ上を repo root と見るので、同じ形に置く
        shutil.copy(SCRIPT, self.root / "bin" / "changelog-release")
        (self.root / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
        self.entries = self.root / "changelog.d"; self.entries.mkdir()
        (self.entries / "README.md").write_text("# 書式\n", encoding="utf-8")

    def cli(self, *args):
        return subprocess.run([sys.executable, str(self.root / "bin" / "changelog-release"), *args],
                              cwd=str(self.root), text=True, capture_output=True)

    def unreleased(self):
        text = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        return text.split("## [Unreleased]", 1)[1].split("## [0.3.0]", 1)[0]

    def test_collect_does_nothing_when_there_are_no_entries(self):
        before = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        r = self.cli("collect")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.root / "CHANGELOG.md").read_text(encoding="utf-8"), before)
        self.assertTrue((self.entries / "README.md").exists())   # README は集約の対象外

    def test_collect_merges_by_heading_in_file_order_and_removes_the_entries(self):
        (self.entries / "240-b.md").write_text("### Fixed\n- 240 の修正\n", encoding="utf-8")
        (self.entries / "239-a.md").write_text("### Added\n- 239 の追加\n\n### Fixed\n- 239 の修正\n", encoding="utf-8")
        r = self.cli("collect")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        body = self.unreleased()
        self.assertIn("- 既にある項目\n- 239 の追加", body)                 # 既存の見出しの末尾に足す
        self.assertIn("- 既にある修正\n- 239 の修正\n- 240 の修正", body)   # ファイル名の昇順
        self.assertEqual(sorted(p.name for p in self.entries.iterdir()), ["README.md"])
        self.assertIn("## [0.3.0] - 2026-09-08", (self.root / "CHANGELOG.md").read_text(encoding="utf-8"))

    def test_collect_dry_run_writes_nothing(self):
        (self.entries / "239-a.md").write_text("### Added\n- 239 の追加\n", encoding="utf-8")
        before = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        r = self.cli("collect", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("239 の追加", r.stdout)
        self.assertEqual((self.root / "CHANGELOG.md").read_text(encoding="utf-8"), before)
        self.assertTrue((self.entries / "239-a.md").exists())

    def test_a_new_heading_is_created_in_keep_a_changelog_order(self):
        (self.entries / "239-a.md").write_text("### Changed\n- 変えた\n\n### Security\n- 塞いだ\n", encoding="utf-8")
        self.assertEqual(self.cli("collect").returncode, 0)
        body = self.unreleased()
        self.assertLess(body.index("### Added"), body.index("### Changed"))
        self.assertLess(body.index("### Changed"), body.index("### Fixed"))
        self.assertLess(body.index("### Fixed"), body.index("### Security"))

    # ---------- check（ゲート）
    def repo(self):
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.name", "aifactory test")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "add", "-A"); git(self.root, "commit", "-q", "-m", "初期")
        git(self.root, "branch", "base")
        git(self.root, "checkout", "-q", "-b", "work")

    def test_check_rejects_a_direct_edit_and_allows_the_release_collect(self):
        (self.entries / "239-a.md").write_text("### Added\n- 239 の追加\n", encoding="utf-8")
        self.repo()
        # 差分なし
        r = self.cli("check", "--base", "base")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # CHANGELOG.md を直接編集した
        (self.root / "CHANGELOG.md").write_text(CHANGELOG.replace("- 既にある項目", "- 既にある項目\n- 直接足した"), encoding="utf-8")
        git(self.root, "commit", "-q", "-am", "CHANGELOG を直接編集")
        r = self.cli("check", "--base", "base")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("FAIL", r.stdout)
        self.assertIn("changelog.d", r.stdout)
        # リリースの集約（changelog.d を消しながら CHANGELOG.md を書く）は通る
        self.assertEqual(self.cli("collect").returncode, 0)
        git(self.root, "add", "-A"); git(self.root, "commit", "-q", "-m", "release: v0.4.0")
        r = self.cli("check", "--base", "base")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_check_skips_when_the_base_ref_is_missing(self):
        self.repo()
        r = self.cli("check", "--base", "origin/does-not-exist")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("飛ばす", r.stdout)


if __name__ == "__main__":
    unittest.main()
