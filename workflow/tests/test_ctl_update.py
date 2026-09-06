"""制御系の配備を 1 コマンドにする bin/ctl-update（チケット 247）。

  python3 -m unittest discover -s workflow/tests -v

VM も systemd も使わない。偽の checkout を作り、PATH の先頭に偽の git / systemctl / curl / sudo を置いて走らせる。
- 汚れた tree では止まり、退避手順を出す（AIFACTORY_LOCAL_TREE=1 で被せた制御系がこれ）
- `._*` / .DS_Store（218 の直接原因）は先に消える
- 手順の順番: fetch → main へ早送り → sandbox CLI → docs → console（restart が最後）
- --ref は detached checkout、--no-docs は mkdocs を飛ばす
- 最後に console / MCP / runner の疎通と配備した版が出る
- 合言葉（CONSOLE_TOKEN）があれば Authorization を 1 引数として渡す
"""
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "bin" / "ctl-update"

# 呼ばれた引数を CALLS に足すだけの偽コマンド。git だけは ctl-update が読む値を返す
FAKE_GIT = r"""#!/usr/bin/env bash
echo "git $*" >> "$CALLS"
case "$1" in
  rev-parse) echo .git ;;
  status) printf '%s' "$FAKE_DIRTY" ;;
  remote) echo "https://example.invalid/x/y.git" ;;
  --no-pager|log) echo "  abc1234 2026-09-07 テスト" ;;
esac
exit 0
"""
FAKE_ANY = r"""#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$CALLS"
exit 0
"""
# 疎通の相手。curl は /api/config を返し、mcp は initialize に答える
FAKE_CURL = r"""#!/usr/bin/env bash
IFS='|'; echo "curl|$*" >> "$CALLS"      # 引数の切れ目が見えるように | でつなぐ
printf '%s' "$FAKE_CONFIG"
"""
FAKE_MCP = r"""#!/usr/bin/env python3
import sys
sys.stdin.readline()
print('{"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"aifactory"}}}')
"""
CONFIG = ('{"workflows":[{"name":"bug"},{"name":"chore"}],"roles":["implementer","reviewer","pm"],'
          '"repo":"/home/aifactory/aifactory"}')


class CtlUpdateTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.calls = self.dir / "calls.log"
        # 偽の checkout（ctl-update が触るファイルだけ）
        self.repo = self.dir / "aifactory"
        for p in ("bin", "sandbox/bin", "console/bin", "website/.venv/bin", "workflow/kit"):
            (self.repo / p).mkdir(parents=True)
        shutil.copy(SCRIPT, self.repo / "bin/ctl-update")
        for p in ("sandbox/bin/install.sh", "console/bin/install.sh", "website/.venv/bin/mkdocs"):
            self._write(self.repo / p, FAKE_ANY)
        self._write(self.repo / "console/bin/mcp", FAKE_MCP)
        (self.repo / "website/mkdocs.yml").write_text("site_name: x\n")
        # PATH の先頭に置く偽コマンド
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self._write(self.bin / "git", FAKE_GIT)
        self._write(self.bin / "curl", FAKE_CURL)
        for c in ("systemctl", "sudo"):
            self._write(self.bin / c, FAKE_ANY)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, path, text):
        path.write_text(text)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def run_ctl(self, *args, dirty="", config=CONFIG):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", HOME=str(self.dir),
                   CALLS=str(self.calls), FAKE_DIRTY=dirty, FAKE_CONFIG=config)
        env.pop("CONSOLE_HOST", None)
        env.pop("CONSOLE_PORT", None)
        return subprocess.run([str(self.repo / "bin/ctl-update"), *args],
                              text=True, capture_output=True, env=env)

    def calls_list(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_dirty_tree_stops_with_recovery_steps(self):
        """汚れた tree では配備せずに止まり、退避 → clone し直しの手順を出す"""
        p = self.run_ctl("--dry-run", dirty=" M CHANGELOG.md\n?? ._bug.yml\n")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("clean ではない", p.stderr)
        self.assertIn("aifactory.pre-clean-", p.stderr)
        self.assertNotIn("git fetch", "\n".join(self.calls_list()))

    def test_removes_appledouble_junk_before_checking(self):
        """._* / .DS_Store は消す（218 の直接原因）。.git の中は触らない"""
        (self.repo / "workflow/kit/._bug.yml").write_bytes(b"\x00\x05\x16\x07Mac OS X")
        (self.repo / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
        (self.repo / ".git").mkdir()
        (self.repo / ".git/._keep").write_bytes(b"\x00")
        p = self.run_ctl("--no-docs")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("workflow/kit/._bug.yml", p.stdout)
        self.assertFalse((self.repo / "workflow/kit/._bug.yml").exists())
        self.assertFalse((self.repo / ".DS_Store").exists())
        self.assertTrue((self.repo / ".git/._keep").exists())

    def test_dry_run_plans_update_then_deploy_in_order(self):
        """順番: fetch → main へ早送り → sandbox CLI → docs → console。dry-run では何も実行しない"""
        p = self.run_ctl("--dry-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.calls_list(), ["git rev-parse --git-dir", "git status --porcelain"],
                         "dry-run で読むだけ以外のコマンドを呼んでいる")
        plan = [l.split("(dry) ", 1)[1] for l in p.stdout.splitlines() if "(dry) " in l]
        self.assertEqual(len(plan), 6, plan)
        self.assertTrue(plan[0].startswith("git fetch"), plan)
        self.assertEqual(plan[1], "git checkout main")
        self.assertEqual(plan[2], "git merge --ff-only origin/main")
        self.assertIn("sandbox/bin/install.sh --systemd", plan[3])
        self.assertIn("mkdocs build -q", plan[4])
        self.assertIn("console/bin/install.sh --systemd", plan[5])
        self.assertIn("bind: 127.0.0.1:8765", p.stdout)

    def test_ref_checks_out_that_version_detached(self):
        p = self.run_ctl("--dry-run", "--ref", "v1.2.0")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("(dry) git checkout --detach v1.2.0", p.stdout)
        self.assertNotIn("git merge", p.stdout)

    def test_no_docs_skips_mkdocs(self):
        p = self.run_ctl("--dry-run", "--no-docs")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("mkdocs", p.stdout)
        self.assertIn("--no-docs なので飛ばす", p.stdout)

    def test_dry_run_keeps_junk(self):
        """--dry-run は何も変えない（消す候補を出すだけ）"""
        (self.repo / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
        p = self.run_ctl("--dry-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(".DS_Store", p.stdout)
        self.assertTrue((self.repo / ".DS_Store").exists())

    def test_verifies_console_mcp_runner_and_prints_version(self):
        """疎通は console / MCP / runner の 3 つ。最後に配備した版を出す"""
        p = self.run_ctl("--no-docs")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("console: ok  workflow 2 / role 3 種", p.stdout)
        self.assertIn("mcp:     ok", p.stdout)
        self.assertIn("runner:  ok", p.stdout)
        self.assertIn("配備した版", p.stdout)
        self.assertIn("abc1234", p.stdout)

    def test_console_token_is_passed_as_one_argument(self):
        """合言葉があれば Authorization ヘッダを付ける（分解されて curl に渡らないこと。ADR-0021）"""
        (self.dir / ".config/aifactory").mkdir(parents=True)
        (self.dir / ".config/aifactory/ctl.env").write_text("CONSOLE_TOKEN=aaa bbb\n")
        p = self.run_ctl("--no-docs")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        curl = [l for l in self.calls_list() if l.startswith("curl|")]
        self.assertTrue(curl, self.calls_list())
        self.assertTrue(curl[0].endswith("|-H|Authorization: Bearer aaa bbb"), curl[0])

    def test_no_token_sends_no_auth_header(self):
        p = self.run_ctl("--no-docs")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        curl = [l for l in self.calls_list() if l.startswith("curl|")]
        self.assertTrue(curl, self.calls_list())
        self.assertNotIn("-H", curl[0])
        self.assertTrue(curl[0].endswith("/api/config"), curl[0])

    def test_junk_in_config_is_an_error(self):
        """出た設定に ._ の項目が残っていたら失敗させる（218 の再発を配備で検知する）"""
        p = self.run_ctl("--no-docs", config='{"workflows":[{"name":"._bug"}],"roles":[],"repo":"x"}')
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("ごみが混ざっている", p.stdout + p.stderr)

    def test_console_unreachable_is_an_error(self):
        p = self.run_ctl("--no-docs", config="")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("応答しない", p.stderr)

    def test_console_bind_follows_installed_unit(self):
        """unit に書いてある bind 先を引き継ぐ（既定に戻すと 10.x の制御系が localhost に落ちる）"""
        unit = self.dir / "aifactory-console.service"
        unit.write_text("[Service]\nEnvironment=CONSOLE_HOST=10.77.0.9\nEnvironment=CONSOLE_PORT=8765\n")
        text = (self.repo / "bin/ctl-update").read_text().replace(
            "UNIT=/etc/systemd/system/aifactory-console.service", f"UNIT={unit}")
        (self.repo / "bin/ctl-update").write_text(text)
        p = self.run_ctl("--dry-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("bind: 10.77.0.9:8765", p.stdout)


if __name__ == "__main__":
    unittest.main()
