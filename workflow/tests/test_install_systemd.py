"""`sandbox/bin/install.sh --systemd` が常駐の timer を全部登録し、`--remove` で全部外すこと（252）。

systemd は使わない。PATH の先頭に偽の systemctl / sudo を置き、HOME と SANDBOX_UNIT_DIR を一時ディレクトリに向ける。
gh-refresh だけを見て書かれていた頃は、2 本目の timer（idle-stop）を足しても登録されない・外れないので、
「テンプレートにある常駐が全部 unit になる」ことを数ではなくファイル名で確かめる。
"""
import os
import pathlib
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
INSTALL = REPO / "sandbox" / "bin" / "install.sh"
TEMPLATES = REPO / "sandbox" / "templates" / "systemd"

# 呼ばれた引数を CALLS に足すだけ。sudo は中身をそのまま実行する（tee / rm / systemctl が本当に動く必要がある）
FAKE_SYSTEMCTL = """#!/usr/bin/env bash
echo "systemctl $*" >> "$CALLS"
exit 0
"""
FAKE_SUDO = """#!/usr/bin/env bash
echo "sudo $*" >> "$CALLS"
exec "$@"
"""


class InstallSystemdTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-install-systemd-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.units = self.dir / "units"; self.units.mkdir()
        self.home = self.dir / "home"; self.home.mkdir()
        self.calls = self.dir / "calls"
        self.bin = self.dir / "bin"; self.bin.mkdir()
        for c, body in (("systemctl", FAKE_SYSTEMCTL), ("sudo", FAKE_SUDO)):
            p = self.bin / c
            p.write_text(body)
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def run_install(self, *args):
        env = dict(os.environ, HOME=str(self.home), CALLS=str(self.calls),
                   SANDBOX_UNIT_DIR=str(self.units), PATH=f"{self.bin}:{os.environ['PATH']}")
        # do_copy の `[ok] installed …（$(cut -c1-40)）` は C ロケールだと日本語の途中で切れる。読めない箇所は置き換えて読む
        return subprocess.run(["bash", str(INSTALL), *args], env=env, encoding="utf-8", errors="replace",
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def calls_text(self):
        return self.calls.read_text() if self.calls.exists() else ""

    def unit_names(self):
        return sorted(p.name for p in self.units.iterdir())

    def expected_units(self):
        """テンプレートにある常駐（<name>.service と <name>.timer の組）"""
        return sorted(p.name for p in TEMPLATES.iterdir() if p.suffix in (".service", ".timer"))

    def test_systemd_installs_every_unit_in_the_templates(self):
        r = self.run_install("--systemd")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.unit_names(), self.expected_units())
        self.assertIn("aifactory-idle-stop.timer", self.unit_names())
        self.assertIn("aifactory-gh-refresh.timer", self.unit_names())

    def test_placeholders_are_filled_in(self):
        self.assertEqual(self.run_install("--systemd").returncode, 0)
        for name in self.unit_names():
            text = (self.units / name).read_text()
            self.assertNotIn("@@", text, name)                       # @@USER@@ / @@HOME@@ / @@PATH@@ が残っていない
        svc = (self.units / "aifactory-idle-stop.service").read_text()
        self.assertIn(f"ExecStart={self.home}/.local/bin/sandbox idle-stop", svc)
        self.assertIn(f"Environment=HOME={self.home}", svc)
        self.assertIn(f"EnvironmentFile=-{self.home}/.config/aifactory/ctl.env", svc)

    def test_every_timer_is_enabled_and_started(self):
        self.assertEqual(self.run_install("--systemd").returncode, 0)
        enabled = re.findall(r"^systemctl enable --now (\S+)$", self.calls_text(), re.M)   # sudo 経由の重複を数えない
        self.assertEqual(sorted(enabled), ["aifactory-gh-refresh.timer", "aifactory-idle-stop.timer", "aifactory-resume.timer"])
        self.assertIn("systemctl daemon-reload", self.calls_text())

    def test_the_cli_is_copied_too(self):
        self.assertEqual(self.run_install("--systemd").returncode, 0)
        self.assertTrue((self.home / ".local/bin/sandbox").exists())

    def test_remove_takes_every_unit_back_out(self):
        self.assertEqual(self.run_install("--systemd").returncode, 0)
        self.calls.unlink()
        r = self.run_install("--remove")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.unit_names(), [])
        disabled = re.findall(r"^systemctl disable --now (\S+)$", self.calls_text(), re.M)
        self.assertEqual(sorted(disabled), ["aifactory-gh-refresh.timer", "aifactory-idle-stop.timer", "aifactory-resume.timer"])

    def test_resume_timer_calls_dispatch_in_the_checkout_every_5_minutes(self):
        """利用枠切れで止まった run の続きは checkout の dispatch --resume-paused が回す（380）。@@REPO@@ はこの checkout に埋まる"""
        self.assertEqual(self.run_install("--systemd").returncode, 0)
        svc = (self.units / "aifactory-resume.service").read_text()
        self.assertIn(f"ExecStart=/usr/bin/python3 {REPO}/glue/bin/dispatch --resume-paused", svc)
        self.assertIn(f"WorkingDirectory={REPO}", svc)
        self.assertIn(f"EnvironmentFile=-{self.home}/.config/aifactory/ctl.env", svc)
        timer = (TEMPLATES / "aifactory-resume.timer").read_text()
        self.assertIn("OnUnitActiveSec=5min", timer)
        self.assertIn("Unit=aifactory-resume.service", timer)

    def test_idle_stop_timer_runs_every_15_minutes(self):
        """止まっている時間が長いほど節電になるが、次の take の待ちは増やせない。15 分ごと"""
        timer = (TEMPLATES / "aifactory-idle-stop.timer").read_text()
        self.assertIn("OnUnitActiveSec=15min", timer)
        self.assertIn("Unit=aifactory-idle-stop.service", timer)
        self.assertIn("WantedBy=timers.target", timer)
