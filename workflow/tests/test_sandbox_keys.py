"""制御系の Claude 鍵プール（keys.json。#379 / ADR-0044）:
名前付きの鍵に「fable 許可 / fable 以外許可」のフラグを持たせ、take / reinject が系統ごとに 1 本ずつ選んで VM に渡すこと。

- `sandbox keys add/list/set/rm/token` が動き、keys.json は 600、トークンの全文はどこにも出ない
- take はフラグの合う鍵のうち last_used が最古のものを選び、選んだ名前を state.json の貸出項目に残す
- 同じ task の reinject は同じ鍵を使い続け、その鍵を使わない設定にしたときだけ選び直す
- 要る用途の鍵が無ければ「鍵なし:」で止まる。env ファイルやプロセスに残った鍵には落ちない（プールが空でも同じ。ADR-0060）

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
                'CLAUDE_TOKEN_FAMILIES="FABLE OPUS SONNET HAIKU"\n'
                # 本物の起動と同じ並び（sandbox/bin/sandbox の冒頭）: プロセスに残った鍵を捨ててから env ファイルを読む。
                # これが無いと「env ファイルの鍵」と「環境に残った古い鍵」を分けて確かめられない（チケット 391）
                'unset CLAUDE_CODE_OAUTH_TOKEN GH_TOKEN CLAUDE_CODE_OAUTH_TOKEN_FABLE CLAUDE_CODE_OAUTH_TOKEN_OPUS CLAUDE_CODE_OAUTH_TOKEN_SONNET CLAUDE_CODE_OAUTH_TOKEN_HAIKU\n'
                'set -a; source "$ENV_FILE"; set +a\n'
                'die() { echo "[error] $*" >&2; exit 1; }\n'
                % (self.dir, self.state, self.keys, self.env_file, os.path.join(self.dir, 'pj'), self.ctl, self.dir))

    def script(self, tail, token_part=False):
        text = SCRIPT.read_text()
        body = text[text.index('# ---------- state（台帳とロック）'):text.index('mask() {')]
        if token_part: body += text[text.index('mask() {'):text.index('cmd_gh_app() {')]
        return self.head() + body + FAKES + tail

    def run_sh(self, tail, *args, stdin=None, token_part=False, env_extra=None):
        # VM の中で走らせるので、この VM 自身の鍵が bash に引き継がれて期待と食い違わないよう落とす。
        # env_extra は「呼び手のプロセスに鍵が残っている」状況を作るため（チケット 391）
        env = {k: v for k, v in os.environ.items() if not k.startswith(('CLAUDE_CODE_OAUTH_TOKEN', 'GH_TOKEN', 'CLAUDE_KEY_NAME'))}
        env.update(env_extra or {})
        return subprocess.run(['bash', '-c', self.script(tail, token_part), 'sandbox', *args], input=stdin, text=True,
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def keys_cmd(self, *args, stdin=None):
        return self.run_sh('cmd_keys "$@"\n', *args, stdin=stdin)

    def take(self, task, pj='pj', need=None):
        """need は要る用途（'fable' / 'other' / 'fable,other'）。runner は workflow から渡す。省略は両方（ADR-0046）"""
        return self.run_sh('cmd_take "$@"\n', pj, task, *([f'--need={need}'] if need else []))

    def reinject(self, task):
        return self.run_sh('cmd_reinject "$@"\n', task, token_part=True)

    def pick(self, task, pj='pj', need=None, current=None, stale_env=None):
        """pull backend の runner が呼ぶ口（チケット 391）。stale_env は呼び手のプロセスに残った古い鍵"""
        args = ['pick', '--pj', pj, '--task', task, '--json']
        if need: args.append('--need=' + need)
        if current is not None: args.append('--current=' + current)
        return self.run_sh('cmd_keys "$@"\n', *args, env_extra=stale_env)

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

    def test_used_counts_launches_apart_from_assignments(self):
        """`keys used <名前>` は runner が claude を起動した回数（launches）を数える。take の割り当て（uses）とは別（2026-09-10）"""
        self.add('fable-main', '--fable', token='fake-token-secret-1234')
        for _ in range(3):
            r = self.keys_cmd('used', 'fable-main')
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('launches 3', r.stdout)
        j = json.loads(self.keys_cmd('list', '--json').stdout)['keys'][0]
        self.assertEqual(j['launches'], 3); self.assertEqual(j['uses'], 0)
        self.assertRegex(j['last_launched'], r'^\d{4}-\d\d-\d\dT')
        r = self.keys_cmd('list')
        self.assertIn('LAUNCHES', r.stdout); self.assertIn('ASSIGNED', r.stdout)

    def test_used_with_an_unknown_name_does_not_fail(self):
        """消した鍵を古い run が報告してきても runner を止めない"""
        r = self.keys_cmd('used', 'gone')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('数えない', r.stdout)

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

    def test_list_on_an_empty_pool_says_runs_pause_until_a_key_is_registered(self):
        r = self.keys_cmd('list')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('鍵なし', r.stdout); self.assertIn('sandbox keys add', r.stdout)
        self.assertNotIn('token set', r.stdout)                                   # env ファイルの鍵の案内はもう無い（ADR-0060）
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
        self.assertEqual(self.take('379', need='other').returncode, 0)
        r = self.keys_cmd('rm', 'k1')
        self.assertNotEqual(r.returncode, 0); self.assertIn('379', r.stderr)
        self.assertEqual(len(self.keys_json()['keys']), 1)
        r = self.keys_cmd('rm', 'k1', '--force')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('sandbox reinject 379', r.stdout)
        self.assertEqual(self.keys_json()['keys'], [])

    def test_disable_points_at_the_leases_that_use_the_key(self):
        self.add('k1', '--fable')
        self.assertEqual(self.take('379', need='fable').returncode, 0)
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
        self.assertEqual(self.take('379', need='fable').returncode, 0)
        self.assertEqual(self.take('380', need='fable').returncode, 0)
        first = self.state_json()['379']['keys']['fable']
        second = self.state_json()['380']['keys']['fable']
        self.assertNotEqual(first, second)
        self.assertEqual(sorted([first, second]), ['fable-a', 'fable-b'])

    def test_reinject_keeps_the_same_key_until_it_is_disabled(self):
        self.add('fable-a', '--fable', token='fake-token-fable-aaaa')
        self.add('fable-b', '--fable', token='fake-token-fable-bbbb')
        self.assertEqual(self.take('379', need='fable').returncode, 0)
        picked = self.state_json()['379']['keys']['fable']
        self.assertEqual(self.reinject('379').returncode, 0)
        self.assertEqual(self.state_json()['379']['keys']['fable'], picked)   # 同じ task は同じ鍵
        self.assertEqual(self.keys_cmd('set', picked, '--disable').returncode, 0)
        self.assertEqual(self.reinject('379').returncode, 0)
        again = self.state_json()['379']['keys']['fable']
        self.assertNotEqual(again, picked)                                    # 使えなくなった鍵は選び直す
        self.assertEqual(self.vm_env()['CLAUDE_KEY_NAME_FABLE'], again)

    def test_a_purpose_without_a_key_stops_the_take_instead_of_using_the_env_key(self):
        """要る用途の鍵が無ければ「鍵なし:」で止まり、VM も予約も残らない（ADR-0046）。env ファイルとプロセスに残った鍵は無いものとして扱う（ADR-0060）"""
        pathlib.Path(self.env_file).write_text('SB_DOMAIN=t.sb.internal\nCLAUDE_CODE_OAUTH_TOKEN_FABLE=envfile-fable\n')
        self.add('opus-a', '--other', token='fake-token-other-bbbb')
        r = self.run_sh('CLAUDE_CODE_OAUTH_TOKEN=env-plain\nCLAUDE_CODE_OAUTH_TOKEN_FABLE=env-fable\ncmd_take "$@"\n', 'pj', '379')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('鍵なし:', r.stderr); self.assertIn('Fable に使う', r.stderr)
        self.assertNotIn('379', self.state_json())                             # 予約は消えている
        self.assertEqual(self.keys_json()['keys'][0]['uses'], 0)               # 止まった回は使用回数を動かさない
        # 要る用途が other だけ（chore など）なら通る。env の鍵は VM に渡らない（無印も other の鍵で埋まる）
        r = self.run_sh('CLAUDE_CODE_OAUTH_TOKEN=env-plain\nCLAUDE_CODE_OAUTH_TOKEN_FABLE=env-fable\ncmd_take "$@"\n', 'pj', '379', '--need=other')
        self.assertEqual(r.returncode, 0, r.stderr)
        env = self.vm_env()
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN'], 'fake-token-other-bbbb')
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN_OPUS'], 'fake-token-other-bbbb')
        self.assertEqual(env.get('CLAUDE_CODE_OAUTH_TOKEN_FABLE', ''), '')
        self.assertNotIn('CLAUDE_KEY_NAME_FABLE', env)
        self.assertEqual(self.state_json()['379']['keys'], {'other': 'opus-a'})

    def test_no_key_anywhere_stops_the_take_too(self):
        """プールが空（ファイルも無い）なら「鍵なし:」で止める。keys.json がまだ無いことも言う（鍵が無い run は一時停止にする）"""
        r = self.run_sh('cmd_take "$@"\n', 'pj', '379')
        self.assertNotEqual(r.returncode, 0); self.assertIn('鍵なし:', r.stderr); self.assertIn('がまだ無い', r.stderr)
        self.assertNotIn('379', self.state_json())

    def test_an_empty_pool_never_falls_back_to_the_env_file_key(self):
        """本件の芯（ADR-0060）: プールが空でも env ファイル・プロセスの鍵で take を通さない。鍵の値はどこにも出ない"""
        pathlib.Path(self.env_file).write_text('SB_DOMAIN=t.sb.internal\nCLAUDE_CODE_OAUTH_TOKEN=envfile-plain-0001\n')
        r = self.run_sh('CLAUDE_CODE_OAUTH_TOKEN=env-plain-0002\ncmd_take "$@"\n', 'pj', '379',
                        env_extra={'CLAUDE_CODE_OAUTH_TOKEN': 'process-plain-0003'})
        self.assertNotEqual(r.returncode, 0); self.assertIn('鍵なし:', r.stderr)
        self.assertIn('Fable に使う', r.stderr); self.assertIn('Opus・Sonnet・Haiku に使う', r.stderr)
        self.assertNotIn('0001', r.stdout + r.stderr); self.assertNotIn('0002', r.stdout + r.stderr); self.assertNotIn('0003', r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'env.10.77.1.1')))   # VM には何も書いていない
        self.assertNotIn('379', self.state_json())

    # ---------- keys pick（pull backend の runner が呼ぶ。チケット 391）
    def test_pick_returns_only_json_with_the_family_variables(self):
        self.add('fable-a', '--fable', token='fake-token-fable-aaaa')
        self.add('opus-a', '--other', token='fake-token-other-bbbb')
        r = self.pick('391')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(r.stdout.strip().splitlines()), 1)          # stdout は JSON 1 行だけ（runner がそのまま読む）
        out = json.loads(r.stdout)
        self.assertEqual(out['keys'], {'fable': 'fable-a', 'other': 'opus-a'})
        env = out['env']
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN_FABLE'], 'fake-token-fable-aaaa')
        self.assertEqual(env['CLAUDE_KEY_NAME_FABLE'], 'fable-a')
        for f in ('OPUS', 'SONNET', 'HAIKU'):
            self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN_' + f], 'fake-token-other-bbbb')
            self.assertEqual(env['CLAUDE_KEY_NAME_' + f], 'opus-a')
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN'], 'fake-token-other-bbbb')
        self.assertEqual(self.keys_json()['keys'][0]['uses'], 1)         # 割り当てとして数える（reinject と同じ意味）

    def test_pick_never_falls_back_to_a_key_in_the_process_environment(self):
        """本件の芯（2026-09-10）: env ファイルに鍵が無くても、プロセスに残った古い鍵は返さない"""
        self.add('opus-a', '--other', token='fake-token-other-bbbb')
        leaked = {'CLAUDE_CODE_OAUTH_TOKEN': 'fake-token-leaked-n5', 'CLAUDE_CODE_OAUTH_TOKEN_OPUS': 'fake-token-leaked-n5'}
        r = self.pick('391', need='other', stale_env=leaked)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('leaked', r.stdout)
        self.assertEqual(json.loads(r.stdout)['env']['CLAUDE_CODE_OAUTH_TOKEN'], 'fake-token-other-bbbb')

    def test_pick_keeps_the_current_key_until_it_is_disabled(self):
        """--current が「前にこの task が使った鍵」。台帳に載らない pull backend の run はここから同じ鍵を使い続ける"""
        self.add('fable-a', '--fable', token='fake-token-fable-aaaa')
        self.add('fable-b', '--fable', token='fake-token-fable-bbbb')
        first = json.loads(self.pick('391', need='fable').stdout)['keys']['fable']
        again = json.loads(self.pick('391', need='fable', current='fable=' + first).stdout)['keys']['fable']
        self.assertEqual(again, first)
        self.assertEqual(self.keys_cmd('set', first, '--disable').returncode, 0)
        r = self.pick('391', need='fable', current='fable=' + first)
        self.assertEqual(r.returncode, 0, r.stderr)
        picked = json.loads(r.stdout)['keys']['fable']
        self.assertNotEqual(picked, first)                                # 無効化したら次の工程から別の鍵
        self.assertEqual(json.loads(r.stdout)['env']['CLAUDE_KEY_NAME_FABLE'], picked)

    def test_pick_stops_with_no_key_and_writes_nothing_to_stdout(self):
        self.add('opus-a', '--other', token='fake-token-other-bbbb')
        r = self.pick('391', stale_env={'CLAUDE_CODE_OAUTH_TOKEN': 'fake-token-leaked-n5'})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('鍵なし:', r.stderr); self.assertIn('Fable に使う', r.stderr)
        self.assertEqual(r.stdout.strip(), '')
        self.assertEqual(self.keys_json()['keys'][0]['uses'], 0)          # 止まった回は使用回数を動かさない

    def test_pick_on_an_empty_pool_stops_instead_of_using_the_env_file_key(self):
        """プールが空でも env ファイルの鍵は返さない（take と同じ規則。ADR-0060）。stdout は空のまま"""
        pathlib.Path(self.env_file).write_text('SB_DOMAIN=t.sb.internal\nCLAUDE_CODE_OAUTH_TOKEN=fake-token-envfile\n')
        r = self.pick('391', stale_env={'CLAUDE_CODE_OAUTH_TOKEN': 'fake-token-leaked-n5'})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('鍵なし:', r.stderr)
        self.assertEqual(r.stdout.strip(), '')
        self.assertNotIn('envfile', r.stderr); self.assertNotIn('leaked', r.stderr)

    def test_pick_needs_json_and_a_task(self):
        r = self.run_sh('cmd_keys "$@"\n', 'pick', '--pj', 'pj', '--task', '391')
        self.assertNotEqual(r.returncode, 0); self.assertIn('keys pick', r.stderr)
        r = self.run_sh('cmd_keys "$@"\n', 'pick', '--json')
        self.assertNotEqual(r.returncode, 0); self.assertIn('keys pick', r.stderr)

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

    def test_token_set_for_claude_is_refused(self):
        """Claude の鍵を env ファイルに置く経路は無い（ADR-0060）。止まって鍵プールを案内し、何も書かない。gh はそのまま"""
        r = self.run_sh('cmd_token set "$@"\n', 'pj', 'claude', stdin='tokREFUSED', token_part=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('sandbox keys add', r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'pj', 'pj.env')))
        r = self.run_sh('cmd_token set "$@"\n', 'pj', 'gh', stdin='ghtok', token_part=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('GH_TOKEN=ghtok', pathlib.Path(self.dir, 'pj', 'pj.env').read_text())

    def test_a_stale_key_line_in_the_env_file_is_ignored_and_reported(self):
        """env ファイルに CLAUDE_CODE_OAUTH_TOKEN= の行が残っていても VM には渡らず、token show が [stale] で場所を言う（値は出さない）"""
        pathlib.Path(self.env_file).write_text('SB_DOMAIN=t.sb.internal\nCLAUDE_CODE_OAUTH_TOKEN=stale-0001\n')
        self.add('opus-a', '--other', token='fake-token-other-bbbb')
        r = self.take('379', need='other')
        self.assertEqual(r.returncode, 0, r.stderr)
        env = self.vm_env()
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN'], 'fake-token-other-bbbb')
        self.assertNotIn('stale', pathlib.Path(self.dir, 'env.10.77.1.1').read_text())
        r = self.run_sh('cmd_token show\n', token_part=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('[stale]', r.stdout); self.assertIn(self.env_file + ': CLAUDE_CODE_OAUTH_TOKEN', r.stdout)
        self.assertNotIn('stale-0001', r.stdout)


if __name__ == '__main__':
    unittest.main()
