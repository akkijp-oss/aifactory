"""モデル系統別の鍵（2026-09-09 オーナー要望）: fable / opus / sonnet の step ごとに別の CLAUDE_CODE_OAUTH_TOKEN を使い分けられること。

- sandbox token set <pj> claude:<系統> が CLAUDE_CODE_OAUTH_TOKEN_<系統> をその PJ のファイルに書く（知らない系統は拒否）
- take が VM に書く /run/sandbox/env には、設定されている系統の鍵だけが CLAUDE_CODE_OAUTH_TOKEN_<系統>=… で載る
- runner の agent_command は step のモデル名から系統を選び、VM の中で `CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN_<系統>:-$CLAUDE_CODE_OAUTH_TOKEN}"`
  を claude の前に置く（系統の鍵が無ければ従来の鍵にそのまま落ちる。runner は鍵の値を持たない）
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


@unittest.skipUnless(shutil.which('jq'), 'jq required by sandbox CLI')
class TokenSetByFamilyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.env = os.path.join(self.dir, 'env'); self.pj_dir = os.path.join(self.dir, 'pj'); os.mkdir(self.pj_dir)
        self.state = os.path.join(self.dir, 'state.json'); self.ctl = os.path.join(self.dir, 'ctl.env')
        pathlib.Path(self.env).write_text('SB_DOMAIN=t.sb.internal\nCLAUDE_CODE_OAUTH_TOKEN=global-token\n')
        pathlib.Path(self.state).write_text('{}')

    def script(self, tail):
        text = SCRIPT.read_text()
        body = text[text.index('mask() {'):text.index('cmd_gh_app() {')]
        head = ('set -euo pipefail\nENV_FILE=%s\nPJ_DIR=%s\nSTATE=%s\nCTL_ENV=%s\nSB_DOMAIN=t.sb.internal\nAPP_PORT=3000\nAPI_MODE=0\nPVE_HOST=x\n'
                '_src_claude="global(env)"\n_src_gh=\ndie() { echo "[error] $*" >&2; exit 1; }\n'
                'load_pj() { CUR_PJ=$1; }\nghapp_ready() { return 1; }\n' % (self.env, self.pj_dir, self.state, self.ctl))
        return head + body + tail

    def run_token(self, *args, stdin=''):
        return subprocess.run(['bash', '-c', self.script('cmd_token "$@"\n'), 'sandbox', *args], input=stdin, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_set_claude_family_writes_the_family_key_only(self):
        r = self.run_token('set', 'alpha', 'claude:fable', stdin='fable-token-1\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        body = pathlib.Path(self.pj_dir, 'alpha.env').read_text()
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN_FABLE=fable-token-1\n', body)
        self.assertNotIn('\nCLAUDE_CODE_OAUTH_TOKEN=', body)                       # 従来の鍵には触らない
        r = self.run_token('clear', 'alpha', 'claude:fable')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN_FABLE', pathlib.Path(self.pj_dir, 'alpha.env').read_text())

    def test_unknown_family_is_refused(self):
        r = self.run_token('set', 'alpha', 'claude:gemini', stdin='x\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('fable|opus|sonnet|haiku', r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.pj_dir, 'alpha.env')))

    def test_show_lists_the_family_keys_that_are_set(self):
        pathlib.Path(self.pj_dir, 'alpha.env').write_text('CLAUDE_CODE_OAUTH_TOKEN_OPUS=opus-token-aaaabbbbcccc\n')
        script = self.script('set -a; source "$PJ_DIR/alpha.env"; set +a\ncmd_token show alpha\n')
        r = subprocess.run(['bash', '-c', script], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN_OPUS: opus-tok', r.stdout)
        self.assertIn('claude-opus-*', r.stdout)
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN_FABLE', r.stdout)                # 未設定の系統は出さない


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
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN_OPUS:-$CLAUDE_CODE_OAUTH_TOKEN}" timeout 60m claude -p', cmd)
        cmd = self.fake_run().agent_command('/home/dev/work/1/p.md', 'claude-fable-5-1', 60)
        self.assertIn('${CLAUDE_CODE_OAUTH_TOKEN_FABLE:-$CLAUDE_CODE_OAUTH_TOKEN}', cmd)

    def test_unknown_model_keeps_the_plain_command(self):
        cmd = self.fake_run().agent_command('/home/dev/work/1/p.md', 'gpt-9', 60)
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', cmd)
        self.assertIn('&& timeout 60m claude -p', cmd)

    def test_the_prefix_falls_back_in_a_real_shell(self):
        """VM と同じ bash で展開して、系統の鍵が無ければ従来の鍵、あれば系統の鍵が claude に渡ること"""
        prefix = self.fake_run().token_env_prefix('claude-opus-5')
        for env, want in (({'CLAUDE_CODE_OAUTH_TOKEN': 'base'}, 'base'),
                          ({'CLAUDE_CODE_OAUTH_TOKEN': 'base', 'CLAUDE_CODE_OAUTH_TOKEN_OPUS': 'opus-only'}, 'opus-only'),
                          ({'CLAUDE_CODE_OAUTH_TOKEN': 'base', 'CLAUDE_CODE_OAUTH_TOKEN_FABLE': 'fable-only'}, 'base')):
            r = subprocess.run(['bash', '-c', prefix + 'printenv CLAUDE_CODE_OAUTH_TOKEN'], text=True, env={'PATH': os.environ['PATH'], **env},
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(r.stdout.strip(), want, r.stderr)


if __name__ == '__main__':
    unittest.main()
