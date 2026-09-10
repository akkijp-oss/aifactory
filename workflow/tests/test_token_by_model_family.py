"""モデル系統別の鍵（2026-09-09 オーナー要望 → ADR-0060 で出どころは鍵プールだけ）: fable / opus / sonnet の step ごとに別の鍵を使うこと。

- take が VM に書く /run/sandbox/env には、鍵プールが用途ごとに選んだ鍵だけが CLAUDE_CODE_OAUTH_TOKEN_<系統>=… で載る
- runner の agent_command は step のモデル名から系統を選び、VM の中で `CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN_<系統>"`
  を claude の前に置く（無印の鍵に落ちる経路は無い。runner は鍵の値を持たない）
- 系統の鍵が VM に無ければ probe が `none` を返し、runner は claude を起動せずに `failure: key` で止める
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'sandbox/bin/sandbox'
import importlib.machinery
import importlib.util
_spec = importlib.util.spec_from_loader("token_family_run", importlib.machinery.SourceFileLoader("token_family_run", str(ROOT / "workflow/bin/run")))
run = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(run)


class InjectEnvFamilyLinesTest(unittest.TestCase):
    """take が VM に書く env（inject_env の heredoc）に、設定された系統の鍵だけが載ること"""

    def lines(self, **env):
        text = SCRIPT.read_text()
        fn = text[text.index('claude_family_lines() {'):text.index('inject_env() {')]
        # VM の中で回すと /run/sandbox/env の CLAUDE_CODE_OAUTH_TOKEN_* が bash に引き継がれて期待と食い違うので、鍵の変数は引き継がない
        base = {k: v for k, v in os.environ.items() if not k.startswith('CLAUDE_CODE_OAUTH_TOKEN')}
        r = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + fn + 'claude_family_lines\n'], text=True,
                           env={**base, **env}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def test_only_configured_families_are_written(self):
        self.assertEqual(self.lines(CLAUDE_CODE_OAUTH_TOKEN_FABLE='f1', CLAUDE_CODE_OAUTH_TOKEN_SONNET='s1'),
                         ['CLAUDE_CODE_OAUTH_TOKEN_FABLE=f1', 'CLAUDE_CODE_OAUTH_TOKEN_SONNET=s1'])

    def test_nothing_when_no_family_key_is_set(self):
        self.assertEqual(self.lines(), [])


class RunnerTokenFamilyTest(unittest.TestCase):
    """runner は鍵の値を知らないまま、VM の中で系統別の鍵に切り替える"""

    def test_family_from_model_name(self):
        f = run.Run.token_family
        self.assertEqual(f('claude-fable-5-1'), 'FABLE')
        self.assertEqual(f('claude-opus-5'), 'OPUS')
        self.assertEqual(f('claude-sonnet-5'), 'SONNET')
        self.assertEqual(f('claude-haiku-4-5-20251001'), 'HAIKU')
        self.assertEqual(f('sonnet'), 'SONNET')
        self.assertEqual(f('gpt-9'), '')

    def fake_run(self):
        r = run.Run.__new__(run.Run); r.project = {}; r.work = '/home/dev/work/1'; return r

    def test_agent_command_selects_the_family_key_inside_the_vm(self):
        cmd = self.fake_run().agent_command('/home/dev/work/1/p.md', 'claude-opus-5', 60)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN_OPUS" timeout 60m claude -p', cmd)
        cmd = self.fake_run().agent_command('/home/dev/work/1/p.md', 'claude-fable-5-1', 60)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN_FABLE"', cmd)
        self.assertNotIn(':-$CLAUDE_CODE_OAUTH_TOKEN', cmd)                        # 無印の鍵に落ちる経路は無い（ADR-0060）

    def test_unknown_model_keeps_the_plain_command(self):
        cmd = self.fake_run().agent_command('/home/dev/work/1/p.md', 'gpt-9', 60)
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', cmd)
        self.assertIn('&& timeout 60m claude -p', cmd)

    def test_the_key_in_the_log_names_the_pool_key_when_take_chose_one(self):
        """ログの key= は名前だけ。take が鍵プール（#379）から選んでいれば `(pool: <名前>)` が付く。系統の鍵が無ければ `none`"""
        cmd = run.Run.key_probe_command('OPUS')
        for env, want in (({'CLAUDE_CODE_OAUTH_TOKEN': 'base'}, 'none'),
                          ({}, 'none'),
                          ({'CLAUDE_CODE_OAUTH_TOKEN_OPUS': 'x'}, 'CLAUDE_CODE_OAUTH_TOKEN_OPUS'),
                          ({'CLAUDE_CODE_OAUTH_TOKEN_OPUS': 'x', 'CLAUDE_KEY_NAME_OPUS': 'opus-a'},
                           'CLAUDE_CODE_OAUTH_TOKEN_OPUS (pool: opus-a)')):
            r = subprocess.run(['bash', '-c', cmd], text=True, env={'PATH': os.environ['PATH'], **env},
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(r.stdout.strip(), want, r.stderr)
            self.assertNotIn('x', r.stdout.replace('CLAUDE_CODE_OAUTH_TOKEN', ''))   # 鍵の値は出さない

    def test_the_prefix_never_falls_back_in_a_real_shell(self):
        """VM と同じ bash で展開して、claude に渡るのは系統の鍵だけ（他の系統の鍵にも無印の鍵にも落ちない）"""
        prefix = self.fake_run().token_env_prefix('claude-opus-5')
        for env, want in (({'CLAUDE_CODE_OAUTH_TOKEN': 'base', 'CLAUDE_CODE_OAUTH_TOKEN_OPUS': 'opus-only'}, 'opus-only'),
                          ({'CLAUDE_CODE_OAUTH_TOKEN': 'base'}, ''),
                          ({'CLAUDE_CODE_OAUTH_TOKEN': 'base', 'CLAUDE_CODE_OAUTH_TOKEN_FABLE': 'fable-only'}, '')):
            r = subprocess.run(['bash', '-c', prefix + 'printenv CLAUDE_CODE_OAUTH_TOKEN'], text=True, env={'PATH': os.environ['PATH'], **env},
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(r.stdout.strip(), want, r.stderr)

    def test_a_missing_family_key_stops_before_claude_is_launched(self):
        """probe が none なら、認証エラーで工程を無駄にせず failure: key（人間待ち。戻しの回数は消費しない）で止める"""
        r = self.fake_run(); r.dry = False; r.task = '379'; r.logs = []; r.log = lambda m: r.logs.append(m)
        r.run_dir = pathlib.Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, r.run_dir, ignore_errors=True)
        r.state = {'history': [], 'attachments': []}; r.wf = {'steps': []}; r.wf_name = 'feature'; r.title = 't'; r.ticket = 'x'
        r.routes = {'MODEL_default': 'claude-opus-5', 'MODEL_coding': 'claude-opus-5'}; r.branch = 'b'; r.base = 'main'
        r.project = {'name': 'p', 'repo': 'o/p', 'app_dir': '/home/dev/app', 'gates': 'g'}
        r.sb = lambda cmd, **kw: 'none\n' if 'CLAUDE_KEY_NAME' in cmd else ''
        launched = []; r.report_key_launch = lambda k: launched.append(k)
        ok, info = r.run_agent({'id': 'implement', 'role': 'implementer'})
        self.assertFalse(ok)
        self.assertEqual(r.last_fail['failure'], 'key')
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN_OPUS', r.last_fail['reason']); self.assertIn('sandbox reinject 379', r.last_fail['reason'])
        self.assertIn('Opus・Sonnet・Haiku に使う', r.last_fail['reason'])
        self.assertEqual(launched, [])                                             # 起動していないので使用回数も報告しない


if __name__ == '__main__':
    unittest.main()


class RunnerReportsPoolLaunchTest(unittest.TestCase):
    """鍵プールの鍵で起動したら `sandbox keys used <名前>` を呼ぶ（uses は割り当て回数で実使用と違った。2026-09-10）"""

    def fake_run(self):
        r = run.Run.__new__(run.Run); r.project = {}; r.work = '/home/dev/work/1'; r.dry = False
        r.logs = []; r.log = lambda m: r.logs.append(m)
        return r

    def test_pool_name_in_the_probe_line_is_reported(self):
        calls = []
        real = run.sh
        run.sh = lambda cmd, **kw: (calls.append(cmd), type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})())[1]
        try:
            r = self.fake_run()
            r.report_key_launch('CLAUDE_CODE_OAUTH_TOKEN_FABLE (pool: novel_akkijp)')
            r.report_key_launch('CLAUDE_CODE_OAUTH_TOKEN_OPUS')             # プール以外は報告しない
            r.report_key_launch('CLAUDE_CODE_OAUTH_TOKEN')
        finally:
            run.sh = real
        self.assertEqual(calls, [['sandbox', 'keys', 'used', 'novel_akkijp']])

    def test_a_failed_report_is_logged_but_does_not_raise(self):
        real = run.sh
        run.sh = lambda cmd, **kw: type('R', (), {'returncode': 1, 'stdout': '', 'stderr': 'x'})()
        try:
            r = self.fake_run()
            r.report_key_launch('CLAUDE_CODE_OAUTH_TOKEN_OPUS (pool: k1)')
        finally:
            run.sh = real
        self.assertTrue(any('keys used k1' in m for m in r.logs))
