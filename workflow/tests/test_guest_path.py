"""ゲストの PATH は「ゲスト自身が答える」（ADR-0076。チケット 549）。

制御系（macos.py / linux.py）が道具を列挙するのをやめ、ログイン環境と版管理ツール（mise）の答えを
そのまま使う形になっていることを見る。VM も claude も mise の実物も使わない: 偽 mise と偽の道具を
一時 dir に置き、前置きを **実際に bash に通して** `command -v` の答えを確かめる
（test_macos.py の guest_sb と同じ「本物の command() を bash に渡す」流儀）。
"""
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'workflow/lib'))
spec = importlib.util.spec_from_file_location('macos_guest_path', ROOT / 'workflow/lib/macos.py')
macos = importlib.util.module_from_spec(spec); spec.loader.exec_module(macos)
import linux  # noqa: E402  （macos を import した後でなければ from macos import ... が解けない）

# worker がゲストでコマンドを起動する形（実装の出典つき）。前置きはこの 2 通りのどちらでも同じ答えを出すこと
MAC_LAUNCH = ['bash', '-lc']                            # workers/.../main.go: tart exec <guest> /bin/bash -lc
LINUX_LAUNCH = ['bash', '--noprofile', '--norc', '-c']  # workers/.../platform_linux.go: systemd-run ... --noprofile --norc -c


def write_exec(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')
    path.chmod(0o755)


class GuestPathTest(unittest.TestCase):
    def make_run(self, module, work='/Users/admin/work/549'):
        """test_macos.py と同じ流儀（object.__new__ + 属性を手で埋める）。lease の配置には依らない"""
        run = object.__new__(module.backend(object))
        run.dry = False
        run.work = work
        run.env_file = work + '/runtime.env'
        run.project = {'app_dir': '/Users/admin/app', 'worker': 'w1'}
        run.state = {}; run.save = lambda: None; run.log = lambda *a: None
        return run

    def guest(self):
        """偽ゲスト。$HOME/.local/bin に偽 mise（保険の固定列挙で見つかる場所）、
        列挙のどこにも無い <tmp>/shims に偽 go を置く。go が見つかったら mise の答えが効いた証拠"""
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
        write_exec(tmp / 'shims/go', '#!/bin/sh\necho fake-go\n')
        write_exec(tmp / '.local/bin/mise',
                   f'#!/bin/sh\nprintf \'export PATH="%s:$PATH"\\n\' {tmp}/shims\n')
        return tmp

    def bash(self, run, cmd, launch=LINUX_LAUNCH, home=None, extra_path=''):
        env = {'HOME': str(home) if home else '/nonexistent-549',
               'PATH': (extra_path + ':' if extra_path else '') + '/usr/bin:/bin',
               'LANG': 'C.UTF-8'}
        return subprocess.run(launch + [run.command(cmd)], text=True, capture_output=True, env=env)

    # ---------- T1: 規則の形（列挙ではなくゲストに訊く）
    def test_the_prelude_asks_the_guest_instead_of_listing_tools(self):
        for name, module in (('macos', macos), ('linux', linux)):
            with self.subTest(backend=name):
                cmd = self.make_run(module).command('true')
                # ログイン環境を起点にし、版管理ツール自身に訊く
                self.assertIn('shopt -q login_shell', cmd)
                self.assertIn('mise activate bash --shims', cmd)
                # shims のパスを制御系が直書きしていない（直書きは「列挙し忘れ」を作り直すことになる）
                self.assertNotIn('mise/shims', cmd)
                # 前置きは rc を握らない形（バックグラウンド化しない。各段は `;` で切り、末尾を
                # 条件付きにしない）。rc がそのまま届くことは T4 / T5 が実行で確かめる
                prelude = self.make_run(module).guest_path_prelude()
                self.assertNotIn(' & ', prelude)
                self.assertTrue(prelude.rstrip().endswith(';'), prelude)

    def test_linux_inherits_the_prelude_and_only_replaces_the_fallback(self):
        """列挙を backend ごとに書き直すと、片方だけ直した道具がもう片方で消える（#549 の go）"""
        self.assertNotIn('command', vars(linux.backend(object)))
        self.assertNotIn('guest_path_prelude', vars(linux.backend(object)))
        self.assertNotEqual(linux.backend(object).PATH_FALLBACK, macos.backend(object).PATH_FALLBACK)
        # 保険の列挙は従来のまま（ログイン環境が PATH を出せないゲストで今までどおり動くこと）
        self.assertIn('/opt/homebrew/bin', macos.backend(object).PATH_FALLBACK)
        self.assertIn('/usr/local/bin', linux.backend(object).PATH_FALLBACK)
        self.assertNotIn('homebrew', linux.backend(object).command.__get__(self.make_run(linux))('true'))

    # ---------- T2: mise の答えが効く（列挙で通ったのではないことの証明）
    def test_a_tool_only_mise_knows_about_resolves(self):
        tmp = self.guest()
        run = self.make_run(macos)
        for name, launch in (('mac', MAC_LAUNCH), ('linux', LINUX_LAUNCH)):
            with self.subTest(launch=name):
                r = self.bash(run, 'command -v go', launch=launch, home=tmp)
                self.assertEqual(r.stdout.strip(), f'{tmp}/shims/go', r.stderr[-500:])

    def test_without_mise_the_enumeration_alone_does_not_find_it(self):
        """同じ前置き・同じ道具で、mise だけ居ないゲスト。<tmp>/shims は列挙に無いので解決しない
        （T2 が固定列挙で通っていたなら、こちらも通ってしまう）"""
        tmp = self.guest()
        (tmp / '.local/bin/mise').unlink()
        r = self.bash(self.make_run(macos), 'command -v go', home=tmp)
        self.assertNotEqual(r.stdout.strip(), f'{tmp}/shims/go')

    # ---------- T3: ゲストのログイン環境が効く（macOS worker と同じ bash -lc）
    def test_a_path_from_the_guest_login_environment_is_kept(self):
        """ゲスト側（ここでは ~/.profile）が足した任意のパスが、制御系の列挙なしで効くこと"""
        tmp = self.guest()
        write_exec(tmp / 'tools/mytool549', '#!/bin/sh\necho mine\n')
        (tmp / '.profile').write_text(f'export PATH="{tmp}/tools:$PATH"\n', encoding='utf-8')
        r = self.bash(self.make_run(macos), 'command -v mytool549', launch=MAC_LAUNCH, home=tmp)
        self.assertEqual(r.stdout.strip(), f'{tmp}/tools/mytool549', r.stderr[-500:])

    # ---------- T4: linux-pull の非ログイン起動でも壊れない
    def test_the_non_login_launch_keeps_the_runtime_env_and_the_exit_code(self):
        tmp = self.guest()
        run = self.make_run(linux, work=str(tmp / 'work'))
        pathlib.Path(run.env_file).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(run.env_file).write_text('export FROM_RUNTIME_ENV=549\n', encoding='utf-8')
        r = self.bash(run, 'echo "$FROM_RUNTIME_ENV"; command -v go', home=tmp)
        self.assertEqual(r.stdout.split(), ['549', f'{tmp}/shims/go'], r.stderr[-500:])
        # 前置きが rc を作り替えない（ゲストの命令の終了コードがそのまま出ること）
        self.assertEqual(self.bash(run, 'exit 3', home=tmp).returncode, 3)

    # ---------- T5: 見つからない道具が rc 0 で素通りしない（完了条件 4）
    def test_a_missing_tool_exits_non_zero_through_the_prelude(self):
        tmp = self.guest()
        for name, launch in (('mac', MAC_LAUNCH), ('linux', LINUX_LAUNCH)):
            with self.subTest(launch=name):
                r = self.bash(self.make_run(macos), 'no-such-tool-549 --version', launch=launch, home=tmp)
                self.assertEqual(r.returncode, 127)
                self.assertIn('not found', r.stderr)

    def test_the_control_plane_sees_the_non_zero_exit_of_a_missing_tool(self):
        """本物の sb() / run_remote() に、本物の command() を bash へ渡す偽 client を差す。
        worker は payload の command をそのまま bash に渡すので、ここで見る形が本番の形
        （#549 の実害の本体は「command not found が rc 0 と読まれる」こと。PATH を直すだけでは次の道具で再発する）"""
        tmp = self.guest()
        run = self.make_run(macos, work=str(tmp / 'work'))
        run.preserve_command = lambda: ''

        def execute(op, payload=None, stdin=None, emit=None):
            r = subprocess.run(['bash', '-c', payload['command']], text=True, capture_output=True,
                               env={'HOME': str(tmp), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
            if emit:
                emit(r.stdout + r.stderr)
            return None, types.SimpleNamespace(returncode=r.returncode, stdout=r.stdout + r.stderr)
        run.client = types.SimpleNamespace(execute=execute)

        with self.assertRaises(RuntimeError) as e:
            run.sb('no-such-tool-549 --version')
        self.assertIn('(127)', str(e.exception))
        # gates はこちらを通る。rc が 0 に化けると「ベースライン緑」と誤読される
        rc, out = run.run_remote('no-such-tool-549 --version', os.devnull)
        self.assertEqual(rc, 127)
        self.assertIn('not found', out)
        self.assertEqual(run.run_remote('true', os.devnull)[0], 0)


if __name__ == '__main__':
    unittest.main()
