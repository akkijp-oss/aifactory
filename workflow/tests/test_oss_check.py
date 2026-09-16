"""bin/oss-check.sh: 追跡ファイルに残った環境固有・私有の名前を弾く公開前チェック（チケット 592）。

  python3 -m unittest discover -s workflow/tests -p 'test_oss_check.py' -v

VM も GitHub も使わない。一時 dir に偽の checkout（bin/oss-check.sh + 必須ファイル + 1 コミット）を作って回す。
- 私有 PJ 名を含む追跡ファイルがあれば非 0（全量経路・`--staged` 経路の両方）
- 例外に名指しした既存の ADR だけは通る（過去の決定記録は書き換えない。ADR-0083）
- 公開許可済みの名前とネットワーク表記は今までどおり通る

検査語そのものはこのファイルに書かない（書くと bin/oss-check.sh 自身がこのテストを弾く）。
workflow/tests/test_scrub_secrets.py と同じく、テストの中で連結して組み立てる。
"""
import pathlib
import shutil
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "bin" / "oss-check.sh"

# 必須ファイル（== 4 節）。中身は問わない
REQUIRED = ["LICENSE", "README.md", "README.ja.md", "CONTRIBUTING.md", "SECURITY.md",
            "CODE_OF_CONDUCT.md", "CHANGELOG.md", ".github/workflows/ci.yml"]

# 例外に載っている既存 ADR のうちの 1 枚（このファイル名自体に私有 PJ 名は無い）
ALLOWED_ADR = "docs/adr/0059-pull-backend-code-steps-run-in-the-guest.md"


class OssCheckTest(unittest.TestCase):
    # 私有 PJ 名（#592 で PAT に足した 2 語）。ソースに直接書かず組み立てる
    PJ_A = "site" + "bin"
    PJ_B = "as" + "ura"

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.root = pathlib.Path(d.name)
        (self.root / "bin").mkdir()
        # スクリプトは自分の 1 つ上を repo root と見るので、同じ形に置く
        shutil.copy(SCRIPT, self.root / "bin" / "oss-check.sh")
        for f in REQUIRED:
            p = self.root / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# fixture\n", encoding="utf-8")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.add_all()
        self.git("commit", "-qm", "fixture")

    def git(self, *args):
        p = subprocess.run(["git", *args], cwd=str(self.root), text=True, capture_output=True)
        if p.returncode: raise AssertionError(f"git {' '.join(args)}: {p.stdout}{p.stderr}")
        return p.stdout

    def add_all(self):
        self.git("add", "bin/oss-check.sh", *REQUIRED)

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def track(self, rel, text):
        """追跡ファイルとして置き、コミットまで済ませる"""
        self.write(rel, text)
        self.git("add", rel)
        self.git("commit", "-qm", "add")

    def check(self, *args):
        return subprocess.run(["bash", str(self.root / "bin" / "oss-check.sh"), *args],
                              cwd=str(self.root), text=True, capture_output=True)

    def test_syntax_is_valid(self):
        r = subprocess.run(["bash", "-n", str(SCRIPT)], text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_clean_checkout_passes(self):
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("NG  ", r.stdout)   # CHANGELOG.md に NG が含まれるので 2 文字では見ない

    def test_a_private_project_name_in_a_tracked_file_fails(self):
        self.track("docs/note.md", f"例として {self.PJ_A} の run を挙げる\n")
        r = self.check()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("NG  ", r.stdout)
        self.assertIn("docs/note.md", r.stdout)

    def test_the_other_private_project_name_in_a_tracked_file_fails(self):
        self.track("workflow/tests/test_x.py", f"# {self.PJ_B} #381 の再発を防ぐ\n")
        r = self.check()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("workflow/tests/test_x.py", r.stdout)

    def test_a_new_adr_is_still_checked(self):
        # 例外はファイル名の名指しなので、新しく足す ADR は今までどおり検査される
        self.track("docs/adr/9999-new-decision.md", f"# 判断\n\n{self.PJ_B} で起きた\n")
        r = self.check()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("docs/adr/9999-new-decision.md", r.stdout)

    def test_an_allowlisted_existing_adr_passes(self):
        self.track(ALLOWED_ADR, f"# 過去の決定\n\n{self.PJ_B} で起きた事実を記録した\n")
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_published_samples_and_network_notation_still_pass(self):
        self.track("docs/ok.md", "kumitate / akkijp/kumitate / akkijp-oss/aifactory\n"
                                 "10.77.1.2\n100.64.0.0/10\n192.168.0.0/16\n")
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_staged_changes_with_a_private_project_name_fail(self):
        self.write("docs/note.md", f"{self.PJ_A} の話\n")
        self.git("add", "docs/note.md")
        r = self.check("--staged")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("NG  ", r.stdout)

    def test_staged_changes_without_a_private_project_name_pass(self):
        self.write("docs/note.md", "ふつうの文章\n")
        self.git("add", "docs/note.md")
        r = self.check("--staged")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_every_allowlisted_path_still_exists_in_this_repo(self):
        """例外に載せたファイルが消えたら例外も落とす（例外が黙って増え続けないように）"""
        src = SCRIPT.read_text(encoding="utf-8")
        line = [ln for ln in src.splitlines() if ln.startswith("ALLOW_FILES=")]
        self.assertEqual(len(line), 1, "ALLOW_FILES が 1 行で定義されていること")
        paths = [p.strip("^$") for p in line[0].split("'", 2)[1].split("|")]
        self.assertTrue(paths)
        for p in paths:
            self.assertTrue((REPO / p.replace("\\.", ".")).is_file(), f"{p} が無い")


if __name__ == "__main__":
    unittest.main()
