"""sandbox に焼く Go の版は workers/go.mod だけを正本にする（チケット 488 / ADR-0071）。

  python3 -m unittest discover -s workflow/tests -v

VM も網も使わない。`bin/go-toolchain.sh` を偽の go.mod と偽の `go` に対して回す。
- required は go.mod の `go` 行から読む（`toolchain` 行があればそちら）
- url は go1.N しか書かれていない go.mod でも配布物の名前（go1.N.0）に直す
- check は go が無い / 古いと非 0、同じか新しいと 0
- provision.sh / prepare.sh / go-toolchain.sh に版を直書きしていない（go.mod を上げたら黙ってずれるため）
- aifactory の PJ 定義は prepare を宣言し、その script が実在する（無いと runner が起動前に止まる）
"""
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "bin" / "go-toolchain.sh"
PJ = REPO / "examples" / "projects" / "aifactory"
GO_MOD = REPO / "workers" / "go.mod"


def required_from_go_mod(path=GO_MOD):
    """テスト側でも go.mod を素直に読む（script と同じ答えになるはず）"""
    ver = ""
    for line in path.read_text().splitlines():
        f = line.split()
        if len(f) >= 2 and f[0] == "toolchain": return f[1].removeprefix("go")
        if len(f) >= 2 and f[0] == "go" and not ver: ver = f[1]
    return ver


BASH = shutil.which("bash") or "/bin/bash"


def run(*args, go_mod=None, path=None):
    env = dict(os.environ)
    if go_mod: env["GO_MOD"] = str(go_mod)
    if path: env["PATH"] = path
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=env)


def path_without_go(dirpath):
    """go だけが無い PATH（script が使う道具だけを symlink で用意する）"""
    d = pathlib.Path(dirpath) / "nogo"
    d.mkdir(parents=True, exist_ok=True)
    for tool in ("awk", "dirname", "id", "uname"):
        src = shutil.which(tool)
        if src: (d / tool).symlink_to(src)
    return str(d)


def fake_go(dirpath, version):
    """`go version` だけ答える偽の go を PATH の先頭に置く"""
    d = pathlib.Path(dirpath) / "bin"
    d.mkdir(parents=True, exist_ok=True)
    exe = d / "go"
    exe.write_text(f'#!/bin/sh\necho "go version go{version} linux/amd64"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return f"{d}:{path_without_go(dirpath)}"


class RequiredVersion(unittest.TestCase):
    def test_reads_the_repository_go_mod_by_default(self):
        want = required_from_go_mod()
        self.assertRegex(want, r"^\d+\.\d+")
        self.assertEqual(run("required").stdout.strip(), want)

    def test_go_line_and_toolchain_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            gm = pathlib.Path(tmp) / "go.mod"
            gm.write_text("module x\n\ngo 1.42.7\n")
            self.assertEqual(run("required", go_mod=gm).stdout.strip(), "1.42.7")
            gm.write_text("module x\n\ngo 1.42.7\n\ntoolchain go1.42.9\n")
            self.assertEqual(run("required", go_mod=gm).stdout.strip(), "1.42.9")

    def test_missing_go_line_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            gm = pathlib.Path(tmp) / "go.mod"
            gm.write_text("module x\n")
            r = run("required", go_mod=gm)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("go 行", r.stderr)


class TarballUrl(unittest.TestCase):
    def test_url_carries_the_required_version(self):
        r = run("url")
        self.assertTrue(r.stdout.startswith("https://go.dev/dl/go" + required_from_go_mod()), r.stdout)

    def test_two_component_version_becomes_the_release_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            gm = pathlib.Path(tmp) / "go.mod"
            gm.write_text("module x\n\ngo 1.42\n")          # go1.21 以降、初版も go1.N.0 という名前で配られる
            self.assertIn("/go1.42.0.", run("url", go_mod=gm).stdout)


class Check(unittest.TestCase):
    def test_no_go_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run("check", path=path_without_go(tmp))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn(required_from_go_mod(), r.stderr)

    def test_older_go_is_rejected_and_says_both_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            gm = pathlib.Path(tmp) / "go.mod"
            gm.write_text("module x\n\ngo 1.42.7\n")
            r = run("check", go_mod=gm, path=fake_go(tmp, "1.42.6"))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("1.42.6", r.stderr)
            self.assertIn("1.42.7", r.stderr)

    def test_same_or_newer_go_passes(self):
        for have in ("1.42.7", "1.42.8", "1.43.0"):
            with self.subTest(have=have), tempfile.TemporaryDirectory() as tmp:
                gm = pathlib.Path(tmp) / "go.mod"
                gm.write_text("module x\n\ngo 1.42.7\n")
                r = run("check", go_mod=gm, path=fake_go(tmp, have))
                self.assertEqual(r.returncode, 0, r.stderr)


class NotHardcoded(unittest.TestCase):
    """版を書いた所が go.mod 以外にあると、go.mod を上げたときに黙ってずれる"""

    def test_scripts_do_not_mention_the_required_version(self):
        want = required_from_go_mod()
        for f in (SCRIPT, PJ / "provision.sh", PJ / "prepare.sh"):
            with self.subTest(file=f.name):
                self.assertNotIn(want, f.read_text())

    def test_provision_and_prepare_go_through_the_one_script(self):
        for f in (PJ / "provision.sh", PJ / "prepare.sh"):
            with self.subTest(file=f.name):
                self.assertIn("bin/go-toolchain.sh", f.read_text())


class PrepareDoesNotStopUnrelatedRuns(unittest.TestCase):
    """prepare.sh は **全 run** が通るので、Go を取れなかっただけで非 0 にしてはいけない（チケット 488 の PM レビュー）。

    非 0 で終わると runner は agent を 1 つも起こさず failure: prepare で run を畳む。Go を 1 行も触らない票
    （文書 / console / kanban）まで道連れになる。「取得できなかった」は握って続行、「取得したが版が足りない」は
    ensure が入れ直して解決する、という切り分けをここで固定する。
    """

    def prepare_in(self, app, toolchain_body):
        """偽の app dir で prepare.sh を回す。bin/go-toolchain.sh は渡した中身に差し替える"""
        app = pathlib.Path(app)
        (app / "bin").mkdir(parents=True, exist_ok=True)
        (app / "workers").mkdir(parents=True, exist_ok=True)
        (app / "bin" / "go-toolchain.sh").write_text(toolchain_body)
        env = dict(os.environ, SANDBOX_APP_DIR=str(app), PATH=path_without_go(app))
        return subprocess.run([BASH, str(PJ / "prepare.sh")], capture_output=True, text=True, env=env)

    def test_a_failed_fetch_does_not_fail_prepare(self):
        with tempfile.TemporaryDirectory() as d:
            # check も ensure も非 0（= 版が足りず、入れ直しにも失敗した＝網が無い回）
            r = self.prepare_in(d, "#!/bin/sh\nexit 1\n")
        self.assertEqual(r.returncode, 0, f"stdout={r.stdout} stderr={r.stderr}")
        self.assertIn("Go を用意できなかった", r.stderr)

    def test_a_satisfied_check_skips_the_install(self):
        with tempfile.TemporaryDirectory() as d:
            # check が 0（= 焼いた Go で足りる）なら ensure は呼ばれない
            r = self.prepare_in(d, '#!/bin/sh\n[ "$1" = check ] && exit 0\necho "ensure が呼ばれた" >&2\nexit 1\n')
        self.assertEqual(r.returncode, 0, f"stdout={r.stdout} stderr={r.stderr}")
        self.assertNotIn("ensure が呼ばれた", r.stderr)

    def test_a_missing_toolchain_script_passes_quietly(self):
        """PJ 定義は checkout と別に配られるので、script が無い版を回す run がありうる"""
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, SANDBOX_APP_DIR=str(d), PATH=path_without_go(d))
            r = subprocess.run([BASH, str(PJ / "prepare.sh")], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, f"stdout={r.stdout} stderr={r.stderr}")


class ProjectDefinition(unittest.TestCase):
    def test_prepare_is_declared_and_present(self):
        yml = (PJ / "project.yml").read_text()
        name = [l.split(":", 1)[1].split("#")[0].strip() for l in yml.splitlines() if l.startswith("prepare:")]
        self.assertEqual(name, ["prepare.sh"], yml)
        self.assertTrue((PJ / name[0]).is_file())


if __name__ == "__main__":
    unittest.main()
