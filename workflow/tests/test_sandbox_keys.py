"""制御系の Claude 鍵プール（keys.json。#379 / ADR-0044）:
名前付きの鍵に「fable 許可 / fable 以外許可」のフラグを持たせ、take / reinject が系統ごとに 1 本ずつ選んで VM に渡すこと。

- `sandbox keys add/list/set/rm/token` が動き、keys.json は 600、トークンの全文はどこにも出ない
- take はフラグの合う鍵のうち last_used が最古のものを選び、選んだ名前を state.json の貸出項目に残す
- 同じ task の reinject は同じ鍵を使い続け、その鍵を使わない設定にしたときだけ選び直す
- 候補が無い系統は PJ / 全体の env の鍵がそのまま残る（互換）。プールが空なら注入される env は今までと同じ

Proxmox / ssh / DNS を叩く関数は偽装し、sandbox/bin/sandbox から区画を切り出して bash で走らせる（test_sandbox_take.py と同じ型）。
"""
import json
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / 'sandbox/bin/sandbox'

FAKES = '''
load_pj() { CUR_PJ=$1; }
pool_list() { echo "9201 sb-t-pj-01 10.77.1.1 stopped"; echo "9202 sb-t-pj-02 10.77.1.2 stopped"; }
pve_has_clean() { return 0; }
rollback() { return 0; }
wait_port() { return 0; }
resolve_gh_token() { return 0; }
dns_set() { return 0; }
dns_del() { return 0; }
vm() { local ip=$1; shift; cat > "$FAKE_ENV_DIR/env.$ip"; }
'''


@unittest.skipUnless(shutil.which('jq'), 'jq required by sandbox CLI')
class SandboxKeysTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.state = os.path.join(self.dir, 'state.json')
        self.keys = os.path.join(self.dir, 'keys.json')
        self.env_file = os.path.join(self.dir, 'env')
        self.ctl = os.path.join(self.dir, 'ctl.env')
        pathlib.Path(self.state).write_text('{}')
        pathlib.Path(self.env_file).write_text('SB_DOMAIN=t.sb.internal\n')

    # ---------- 切り出し
    def head(self):
        return ('set -euo pipefail\n'
                'CONF_DIR=%s\nSTATE=%s\nSANDBOX_KEYS=%s\nENV_FILE=%s\nPJ_DIR=%s\nCTL_ENV=%s\nFAKE_ENV_DIR=%s\n'
                'SB_DOMAIN=t.sb.internal\nAPP_PORT=3000\nAPI_MODE=0\nPVE_HOST=x\nSB_JUMP=\n'
                '_src_claude=""\n_src_gh=""\nGH_TOKEN=\nGH_REPO=\n'
                'die() { echo "[error] $*" >&2; exit 1; }\n'
                % (self.dir, self.state, self.keys, self.env_file, os.path.join(self.dir, 'pj'), self.ctl, self.dir))

    def script(self, tail, token_part=False):
        text = SCRIPT.read_text()
        body = text[text.index('# ---------- state（台帳とロック）'):text.index('mask() {')]
        if token_part: body += text[text.index('mask() {'):text.index('cmd_gh_app() {')]
        return self.head() + body + FAKES + tail

    def run_sh(self, tail, *args, stdin=None, token_part=False):
        # VM の中で走らせるので、この VM 自身の鍵が bash に引き継がれて期待と食い違わないよう落とす
        env = {k: v for k, v in os.environ.items() if not k.startswith(('CLAUDE_CODE_OAUTH_TOKEN', 'GH_TOKEN', 'CLAUDE_KEY_NAME'))}
        return subprocess.run(['bash', '-c', self.script(tail, token_part), 'sandbox', *args], input=stdin, text=True,
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def keys_cmd(self, *args, stdin=None):
        return self.run_sh('cmd_keys "$@"\n', *args, stdin=stdin)

    def take(self, task, pj='pj'):
        return self.run_sh('cmd_take "$@"\n', pj, task)

    def reinject(self, task):
        return self.run_sh('cmd_reinject "$@"\n', task, token_part=True)

    def add(self, name, *flags, token='fake-token-token', note=None):
        args = ['add', name, *flags] + (['--note', note] if note else [])
        r = self.keys_cmd(*args, stdin=token + '\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def keys_json(self):
        return json.loads(pathlib.Path(self.keys).read_text())

    def state_json(self):
        return json.loads(pathlib.Path(self.state).read_text())

    def vm_env(self, ip='10.77.1.1'):
        return dict(l.split('=', 1) for l in pathlib.Path(self.dir, 'env.' + ip).read_text().splitlines() if '=' in l)

    # ---------- CLI
    def test_add_writes_a_600_file_and_never_prints_the_token(self):
        r = self.add('fable-main', '--fable', token='fake-token-secret-value', note='Fable 用')
        self.assertIn('fable-main', r.stdout)
        self.assertNotIn('secret-value', r.stdout + r.stderr)
        self.assertEqual(stat.S_IMODE(os.stat(self.keys).st_mode), 0o600)
        k = self.keys_json()['keys'][0]
        self.assertEqual(k['name'], 'fable-main')
        self.assertEqual(k['allow'], {'fable': True, 'other': False})
        self.assertTrue(k['enabled']); self.assertEqual(k['uses'], 0); self.assertIsNone(k['last_used'])
        self.assertEqual(k['note'], 'Fable 用')

    def test_list_shows_the_flags_and_only_the_last_four_characters(self):
        self.add('fable-main', '--fable', token='fake-token-secret-1234')
        self.add('both', '--fable', '--other', token='fake-token-secret-5678')
        r = self.keys_cmd('list')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('fable-main', r.stdout); self.assertIn('1234', r.stdout)
        self.assertNotIn('secret-1234', r.stdout); self.assertNotIn('secret-5678', r.stdout)
        j = json.loads(self.keys_cmd('list', '--json').stdout)
        self.assertEqual([k['name'] for k in j['keys']], ['fable-main', 'both'])
        self.assertEqual(j['keys'][1]['allow'], {'fable': True, 'other': True})
        self.assertNotIn('token', j['keys'][0])
        self.assertEqual(j['keys'][0]['tail4'], '1234')

    def test_list_on_an_empty_pool_says_the_env_key_is_used(self):
        r = self.keys_cmd('list')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('env', r.stdout)
        self.assertFalse(os.path.exists(self.keys))

    def test_a_duplicate_name_and_a_bad_name_are_refused(self):
        self.add('fable-main', '--fable')
        r = self.keys_cmd('add', 'fable-main', '--fable', stdin='x\n')
        self.assertNotEqual(r.returncode, 0); self.assertIn('既にあります', r.stderr)
        r = self.keys_cmd('add', 'bad name!', '--fable', stdin='x\n')
        self.assertNotEqual(r.returncode, 0); self.assertIn('1〜40', r.stderr)
        r = self.keys_cmd('add', 'noflag', stdin='x\n')
        self.assertNotEqual(r.returncode, 0); self.assertIn('--fable', r.stderr)
        self.assertEqual([k['name'] for k in self.keys_json()['keys']], ['fable-main'])

    def test_set_changes_the_flags_and_disables(self):
        self.add('k1', '--fable')
        self.assertEqual(self.keys_cmd('set', 'k1', '--other=on', '--fable=off', '--note', 'メモ').returncode, 0)
        k = self.keys_json()['keys'][0]
        self.assertEqual(k['allow'], {'fable': False, 'other': True}); self.assertEqual(k['note'], 'メモ')
        self.assertEqual(self.keys_cmd('set', 'k1', '--disable').returncode, 0)
        self.assertFalse(self.keys_json()['keys'][0]['enabled'])
        self.assertEqual(self.keys_cmd('set', 'k1', '--enable').returncode, 0)
        self.assertTrue(self.keys_json()['keys'][0]['enabled'])

    def test_token_replaces_the_value_without_printing_it(self):
        self.add('k1', '--other', token='fake-token-old-0001')
        r = self.keys_cmd('token', 'k1', stdin='fake-token-new-0002\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('0002', r.stdout + r.stderr)
        self.assertEqual(self.keys_json()['keys'][0]['token'], 'fake-token-new-0002')

    def test_rm_of_a_key_in_use_needs_force(self):
        self.add('k1', '--other')
        self.assertEqual(self.take('379').returncode, 0)
        r = self.keys_cmd('rm', 'k1')
        self.assertNotEqual(r.returncode, 0); self.assertIn('379', r.stderr)
        self.assertEqual(len(self.keys_json()['keys']), 1)
        r = self.keys_cmd('rm', 'k1', '--force')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('sandbox reinject 379', r.stdout)
        self.assertEqual(self.keys_json()['keys'], [])

    def test_disable_points_at_the_leases_that_use_the_key(self):
        self.add('k1', '--fable')
        self.assertEqual(self.take('379').returncode, 0)
        r = self.keys_cmd('set', 'k1', '--disable')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('sandbox reinject 379', r.stdout)

    # ---------- take / reinject
    def test_take_picks_one_key_per_family_and_records_the_names(self):
        self.add('fable-a', '--fable', token='fake-token-fable-aaaa')
        self.add('opus-a', '--other', token='fake-token-other-bbbb')
        r = self.take('379')
        self.assertEqual(r.returncode, 0, r.stderr)
        env = self.vm_env()
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN_FABLE'], 'fake-token-fable-aaaa')
        for f in ('OPUS', 'SONNET', 'HAIKU'):
            self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN_' + f], 'fake-token-other-bbbb')
            self.assertEqual(env['CLAUDE_KEY_NAME_' + f], 'opus-a')
        self.assertEqual(env['CLAUDE_KEY_NAME_FABLE'], 'fable-a')
        # 無印の鍵が env に無いときは other の鍵で埋める（プールだけで運用できるように）
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN'], 'fake-token-other-bbbb')
        self.assertEqual(self.state_json()['379']['keys'], {'fable': 'fable-a', 'other': 'opus-a'})
        k = {x['name']: x for x in self.keys_json()['keys']}
        self.assertEqual(k['fable-a']['uses'], 1); self.assertIsNotNone(k['fable-a']['last_used'])

    def test_the_next_take_picks_the_least_recently_used_key(self):
        self.add('fable-a', '--fable', token='fake-token-fable-aaaa')
        self.add('fable-b', '--fable', token='fake-token-fable-bbbb')
        self.assertEqual(self.take('379').returncode, 0)
        self.assertEqual(self.take('380').returncode, 0)
        first = self.state_json()['379']['keys']['fable']
        second = self.state_json()['380']['keys']['fable']
        self.assertNotEqual(first, second)
        self.assertEqual(sorted([first, second]), ['fable-a', 'fable-b'])

    def test_reinject_keeps_the_same_key_until_it_is_disabled(self):
        self.add('fable-a', '--fable', token='fake-token-fable-aaaa')
        self.add('fable-b', '--fable', token='fake-token-fable-bbbb')
        self.assertEqual(self.take('379').returncode, 0)
        picked = self.state_json()['379']['keys']['fable']
        self.assertEqual(self.reinject('379').returncode, 0)
        self.assertEqual(self.state_json()['379']['keys']['fable'], picked)   # 同じ task は同じ鍵
        self.assertEqual(self.keys_cmd('set', picked, '--disable').returncode, 0)
        self.assertEqual(self.reinject('379').returncode, 0)
        again = self.state_json()['379']['keys']['fable']
        self.assertNotEqual(again, picked)                                    # 使えなくなった鍵は選び直す
        self.assertEqual(self.vm_env()['CLAUDE_KEY_NAME_FABLE'], again)

    def test_a_family_without_a_candidate_falls_back_to_the_env_key(self):
        pathlib.Path(self.env_file).write_text('SB_DOMAIN=t.sb.internal\n')
        self.add('opus-a', '--other', token='fake-token-other-bbbb')
        r = self.run_sh('CLAUDE_CODE_OAUTH_TOKEN=env-plain\nCLAUDE_CODE_OAUTH_TOKEN_FABLE=env-fable\ncmd_take "$@"\n', 'pj', '379')
        self.assertEqual(r.returncode, 0, r.stderr)
        env = self.vm_env()
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN_FABLE'], 'env-fable')   # fable の候補が無いので env のまま
        self.assertNotIn('CLAUDE_KEY_NAME_FABLE', env)
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN'], 'env-plain')         # 無印は env の値が勝つ
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN_OPUS'], 'fake-token-other-bbbb')
        self.assertEqual(self.state_json()['379']['keys'], {'other': 'opus-a'})

    def test_an_empty_pool_writes_exactly_what_it_used_to(self):
        r = self.run_sh('CLAUDE_CODE_OAUTH_TOKEN=env-plain\ncmd_take "$@"\n', 'pj', '379')
        self.assertEqual(r.returncode, 0, r.stderr)
        body = pathlib.Path(self.dir, 'env.10.77.1.1').read_text()
        self.assertNotIn('CLAUDE_KEY_NAME', body)
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN_', body)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN=env-plain\n', body)
        self.assertNotIn('keys', self.state_json()['379'])

    def test_token_show_counts_the_pool(self):
        self.add('fable-a', '--fable')
        self.add('both', '--fable', '--other')
        self.assertEqual(self.keys_cmd('set', 'both', '--disable').returncode, 0)
        r = self.run_sh('cmd_token show\n', token_part=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('pool: 2 本', r.stdout)
        self.assertIn('有効 1', r.stdout)
        self.assertIn('Fable に使える 1', r.stdout)                                  # 用途の言い方は console の「鍵」画面と同じ

    def test_list_names_the_purposes_like_the_console(self):
        self.add('fable-a', '--fable')
        r = self.keys_cmd('list')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('OPUS/SONNET', r.stdout); self.assertIn('ENABLED', r.stdout)
        self.assertIn('Fable に使う', r.stdout)                                      # 末尾の凡例

    def test_token_set_for_claude_is_deprecated_but_still_works(self):
        """Claude の鍵を env ファイルに置く方式は非推奨（ADR-0045）。注意を stderr に出すが保存はする。gh には出さない"""
        r = self.run_sh('cmd_token set "$@"\n', 'pj', 'claude', stdin='tokDEPR', token_part=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('[非推奨]', r.stderr); self.assertIn('sandbox keys add', r.stderr)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN=tokDEPR', pathlib.Path(self.dir, 'pj', 'pj.env').read_text())
        r = self.run_sh('cmd_token set "$@"\n', 'pj', 'gh', stdin='ghtok', token_part=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('[非推奨]', r.stderr)


if __name__ == '__main__':
    unittest.main()
