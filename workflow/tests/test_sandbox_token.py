"""sandbox token（ADR-0029 → ADR-0060）: env ファイルに置けるのは GitHub の静的トークンだけになったこと。

- `token set|clear <pj|global> gh` は今までどおり（600、発行日コメント、貸出中の task への案内）
- `token set|clear … claude` は保存せずに止まり、鍵プール（sandbox keys add / console の「鍵」画面）を案内する
- `token rotate gh` は global・GH_TOKEN を持つ全 PJ・ctl.env を 1 回の入力で差し替え、console restart と reinject --all まで行う
- `token rotate claude` は制御系の ctl.env（intake が使う鍵）だけを差し替える。env / pj ファイルにも貸出中の VM にも触らない
- `token show` は VM に渡す Claude の鍵が鍵プールだけであることと、env ファイルに残った Claude の鍵の行（使われない）を言う

sandbox/bin/sandbox から mask 〜 cmd_reinject の区画だけを切り出し、Proxmox / ssh / systemd を偽装して bash で走らせる。
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / 'sandbox/bin/sandbox'
TOKEN = 'dummy-token-aaaabbbbccccdddd-0001'
TOKEN2 = 'dummy-token-eeeeffffgggghhhh-0002'

# VM / systemd を叩くものは関数で置き換える。inject_env は呼ばれた task を CALLS に追記する
FAKES = '''
load_pj() { CUR_PJ=$1; }
state_field() { echo "x"; }
touch_used() { :; }   # 最終利用の記録（252）。切り出しの外にあるので空にする
pool_apply_keys() { :; }; _pool_need_from_state() { :; }; pool_require_keys() { :; }   # 鍵プール（379 / ADR-0046）。切り出しの外にあるので空にする
state_set_keys() { :; }
ghapp_ready() { return 1; }
inject_env() { echo "$2" >> "$CALLS"; return 0; }
systemctl() { echo "systemctl $*" >> "$CALLS"; [[ "$1" == list-unit-files ]] && echo "aifactory-console.service enabled"; return 0; }
sudo() { if [[ "$1" == -n ]]; then return "$SUDO_RC"; fi; echo "sudo $*" >> "$CALLS"; "$@"; }
'''


@unittest.skipUnless(shutil.which('jq'), 'jq required by sandbox CLI')
class SandboxTokenTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.env = os.path.join(self.dir, 'env')
        self.pj_dir = os.path.join(self.dir, 'pj')
        self.ctl = os.path.join(self.dir, 'ctl.env')
        self.state = os.path.join(self.dir, 'state.json')
        self.keys = os.path.join(self.dir, 'keys.json')
        self.calls = os.path.join(self.dir, 'calls')
        os.mkdir(self.pj_dir)
        pathlib.Path(self.env).write_text('SB_DOMAIN=t.sb.internal\n')
        pathlib.Path(self.state).write_text('{}')
        self.write_pj('alpha', 'GH_REPO=o/alpha\nGH_TOKEN=old-gh-alpha\n')
        self.write_pj('bravo', 'GH_REPO=o/bravo\nGH_TOKEN=old-gh-bravo\n')
        self.write_pj('charlie', 'GH_REPO=o/charlie\n')          # GH_TOKEN を持たない PJ
        self.write_ctl()

    def write_pj(self, pj, body):
        pathlib.Path(self.pj_dir, pj + '.env').write_text('# sandbox PJ 別設定: %s\n%s' % (pj, body))

    def write_ctl(self):
        pathlib.Path(self.ctl).write_text(
            '# 制御系のプロセスに渡す環境\nCONSOLE_TOKEN=console-secret\nCONSOLE_PORT=8765\n'
            '# intake が claude -p を呼ぶ用\nCLAUDE_CODE_OAUTH_TOKEN=old-ctl\nGH_TOKEN=old-gh-ctl\n')

    def script(self, extra=''):
        text = SCRIPT.read_text()
        body = text[text.index('mask() {'):text.index('cmd_gh_app() {')]
        head = ('set -euo pipefail\n'
                'ENV_FILE=%s\nPJ_DIR=%s\nSTATE=%s\nKEYS_FILE=%s\nCTL_ENV=${SANDBOX_CTL_ENV:-%s}\n'
                'SB_DOMAIN=t.sb.internal\nAPP_PORT=3000\nAPI_MODE=0\nPVE_HOST=x\n'
                '_src_gh=\n'
                'die() { echo "[error] $*" >&2; exit 1; }\n'
                % (self.env, self.pj_dir, self.state, self.keys, self.ctl))
        return head + body + FAKES + extra

    def run_sandbox(self, args, stdin='', sudo_rc='0', ctl_env=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('CLAUDE_CODE_OAUTH_TOKEN', 'GH_TOKEN', 'CLAUDE_KEY_NAME'))}
        env.update(CALLS=self.calls, SUDO_RC=sudo_rc)
        if ctl_env is not None:
            env['SANDBOX_CTL_ENV'] = ctl_env
        script = self.script() + 'cmd_token "$@"\n'
        return subprocess.run(['bash', '-c', script, 'sandbox'] + args, input=stdin, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

    def read(self, path):
        return pathlib.Path(path).read_text()

    def calls_text(self):
        return self.read(self.calls) if os.path.exists(self.calls) else ''

    # ---------- rotate gh（ADR-0029 のまま）

    def test_rotate_gh_updates_global_and_every_pj_holding_the_key(self):
        r = self.run_sandbox(['rotate', 'gh'], stdin=TOKEN)
        self.assertEqual(r.returncode, 0, r.stderr)
        for f in (self.env, os.path.join(self.pj_dir, 'alpha.env'), os.path.join(self.pj_dir, 'bravo.env')):
            self.assertIn('GH_TOKEN=%s\n' % TOKEN, self.read(f), f)
            self.assertIn('[updated] %s' % f, r.stdout)
        self.assertNotIn('GH_TOKEN', self.read(os.path.join(self.pj_dir, 'charlie.env')))
        self.assertIn('[skip]', r.stdout)
        self.assertIn('4 箇所更新 / 1 箇所 skip', r.stdout)     # global + alpha + bravo + ctl.env
        self.assertNotIn('old-gh-alpha', self.read(os.path.join(self.pj_dir, 'alpha.env')))
        self.assertNotIn(TOKEN, r.stdout)                       # 生のトークンは出さない（マスクのみ）

    def test_rotate_gh_replaces_only_the_gh_line_in_ctl_env(self):
        r = self.run_sandbox(['rotate', 'gh'], stdin=TOKEN)
        self.assertEqual(r.returncode, 0, r.stderr)
        ctl = self.read(self.ctl)
        self.assertIn('CONSOLE_TOKEN=console-secret', ctl)      # 同居する鍵を消さない
        self.assertIn('CONSOLE_PORT=8765', ctl)
        self.assertIn('# 制御系のプロセスに渡す環境', ctl)       # コメントも残す
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN=old-ctl', ctl)   # intake の Claude の鍵には触らない
        self.assertIn('GH_TOKEN=%s' % TOKEN, ctl)
        self.assertNotIn('old-gh-ctl', ctl)

    def test_rotate_gh_records_one_issued_comment_per_file(self):
        self.assertEqual(self.run_sandbox(['rotate', 'gh'], stdin=TOKEN).returncode, 0)
        self.assertEqual(self.run_sandbox(['rotate', 'gh'], stdin=TOKEN2).returncode, 0)
        for f in (self.env, self.ctl, os.path.join(self.pj_dir, 'alpha.env')):
            lines = [l for l in self.read(f).splitlines() if l.startswith('# GH_TOKEN issued: ')]
            self.assertEqual(len(lines), 1, self.read(f))
            self.assertRegex(lines[0], r'issued: \d{4}-\d{2}-\d{2}$')
        self.assertIn('GH_TOKEN=%s\n' % TOKEN2, self.read(self.ctl))

    def test_rotate_gh_restarts_console_and_reinjects_every_lent_task(self):
        pathlib.Path(self.state).write_text(
            '{"101":{"vmid":9201,"ip":"10.77.1.1","pj":"alpha"},'
            ' "102":{"vmid":9202,"ip":"10.77.1.2","pj":"bravo"}}')
        r = self.run_sandbox(['rotate', 'gh'], stdin=TOKEN)
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.calls_text()
        self.assertIn('systemctl restart aifactory-console', calls)
        self.assertIn('[ok] aifactory-console を restart した', r.stdout)
        self.assertEqual(sorted(l for l in calls.splitlines() if l in ('101', '102')), ['101', '102'])

    def test_rotate_gh_prints_the_command_when_passwordless_sudo_is_unavailable(self):
        r = self.run_sandbox(['rotate', 'gh'], stdin=TOKEN, sudo_rc='1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('sudo systemctl restart aifactory-console', r.stdout)
        self.assertNotIn('systemctl restart', self.calls_text())
        self.assertIn('GH_TOKEN=%s' % TOKEN, self.read(self.ctl))   # 更新自体は済んでいる

    def test_rotate_gh_warns_but_continues_when_the_host_has_no_ctl_env(self):
        missing = os.path.join(self.dir, 'no-such-ctl.env')
        r = self.run_sandbox(['rotate', 'gh'], stdin=TOKEN, ctl_env=missing)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('[warn]', r.stdout)
        self.assertIn('制御系 LXC', r.stdout)
        self.assertIn('GH_TOKEN=%s\n' % TOKEN, self.read(self.env))
        self.assertNotIn('systemctl restart', self.calls_text())
        self.assertFalse(os.path.exists(missing))

    # ---------- rotate claude（ADR-0060: ctl.env だけ）

    def test_rotate_claude_touches_only_ctl_env_and_restarts_the_console(self):
        """VM に渡す鍵はプールなので、env / pj ファイルには書かず、貸出中の VM にも再注入しない"""
        pathlib.Path(self.state).write_text('{"101":{"vmid":9201,"ip":"10.77.1.1","pj":"alpha"}}')
        r = self.run_sandbox(['rotate', 'claude'], stdin=TOKEN)
        self.assertEqual(r.returncode, 0, r.stderr)
        ctl = self.read(self.ctl)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN=%s\n' % TOKEN, ctl)
        self.assertNotIn('old-ctl', ctl)
        self.assertIn('CONSOLE_TOKEN=console-secret', ctl); self.assertIn('GH_TOKEN=old-gh-ctl', ctl)
        self.assertIn('[updated] %s' % self.ctl, r.stdout)
        self.assertNotIn(TOKEN, r.stdout)
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', self.read(self.env))
        for pj in ('alpha', 'bravo', 'charlie'):
            self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', self.read(os.path.join(self.pj_dir, pj + '.env')))
        calls = self.calls_text()
        self.assertIn('systemctl restart aifactory-console', calls)
        self.assertNotIn('101', calls.splitlines())                # reinject は起きない
        self.assertIn('sandbox keys token', r.stdout)              # プールの鍵はこちら、と案内する

    def test_rotate_claude_family_writes_the_family_line_in_ctl_env(self):
        r = self.run_sandbox(['rotate', 'claude:fable'], stdin=TOKEN)
        self.assertEqual(r.returncode, 0, r.stderr)
        ctl = self.read(self.ctl)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN_FABLE=%s\n' % TOKEN, ctl)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN=old-ctl', ctl)     # 無印には触らない

    def test_rotate_claude_refuses_outside_the_control_plane(self):
        """ctl.env が無いホストには差し替える先が無い。VM の鍵を替えたいなら sandbox keys token と言って止まる"""
        missing = os.path.join(self.dir, 'no-such-ctl.env')
        r = self.run_sandbox(['rotate', 'claude'], stdin=TOKEN, ctl_env=missing)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('sandbox keys token', r.stderr)
        self.assertFalse(os.path.exists(missing))
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', self.read(self.env))

    def test_rotate_refuses_an_empty_token(self):
        r = self.run_sandbox(['rotate', 'claude'], stdin='\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('空のトークン', r.stderr)
        self.assertIn('old-ctl', self.read(self.ctl))

    def test_rotate_rejects_an_unknown_kind(self):
        r = self.run_sandbox(['rotate', 'slack'], stdin=TOKEN)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('gh か claude', r.stderr)

    # ---------- set / clear（gh だけ）

    def test_set_gh_for_a_project_without_a_lent_task_exits_zero(self):
        """貸出中の task が無いと [hint] の && が偽になり、set が終了コード 1 を返していた"""
        r = self.run_sandbox(['set', 'delta', 'gh'], stdin=TOKEN)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        body = self.read(os.path.join(self.pj_dir, 'delta.env'))
        self.assertIn('GH_TOKEN=%s\n' % TOKEN, body)
        self.assertEqual(oct(os.stat(os.path.join(self.pj_dir, 'delta.env')).st_mode & 0o777), '0o600')

    def test_set_gh_twice_keeps_a_single_issued_line(self):
        self.assertEqual(self.run_sandbox(['set', 'alpha', 'gh'], stdin=TOKEN).returncode, 0)
        self.assertEqual(self.run_sandbox(['set', 'alpha', 'gh'], stdin=TOKEN2).returncode, 0)
        body = self.read(os.path.join(self.pj_dir, 'alpha.env'))
        self.assertEqual(body.count('# GH_TOKEN issued: '), 1, body)
        self.assertEqual(body.count('GH_TOKEN='), 1, body)   # 値の行は 1 本だけ
        self.assertIn('GH_TOKEN=%s\n' % TOKEN2, body)
        self.assertIn('GH_REPO=o/alpha', body)

    def test_clear_gh_removes_the_value_and_the_issued_line(self):
        self.assertEqual(self.run_sandbox(['set', 'alpha', 'gh'], stdin=TOKEN).returncode, 0)
        self.assertEqual(self.run_sandbox(['clear', 'alpha', 'gh'], stdin='').returncode, 0)
        body = self.read(os.path.join(self.pj_dir, 'alpha.env'))
        self.assertNotIn('GH_TOKEN', body)
        self.assertIn('GH_REPO=o/alpha', body)

    def test_set_claude_is_refused_and_writes_nothing(self):
        """Claude の鍵を env ファイルに置く経路は無い（ADR-0060）。既定の kind（省略）も claude 扱いで止める"""
        for kind in ([], ['claude'], ['claude:fable']):
            r = self.run_sandbox(['set', 'alpha'] + kind, stdin=TOKEN)
            self.assertNotEqual(r.returncode, 0, kind)
            self.assertIn('sandbox keys add', r.stderr)
            self.assertIn('token rotate claude', r.stderr)             # intake 用の ctl.env はこちら
            self.assertNotIn(TOKEN, self.read(os.path.join(self.pj_dir, 'alpha.env')))
            self.assertNotIn(TOKEN, self.read(self.env))
        r = self.run_sandbox(['clear', 'global', 'claude'])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('SB_DOMAIN=t.sb.internal', self.read(self.env))

    # ---------- show

    def test_show_names_the_pool_as_the_only_source_and_the_intake_key(self):
        self.assertEqual(self.run_sandbox(['rotate', 'claude'], stdin=TOKEN).returncode, 0)
        r = self.run_sandbox(['show'])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('host: 制御系（ctl.env あり）', r.stdout)
        self.assertIn('鍵プール', r.stdout)
        self.assertIn('env ファイルの鍵は使わない', r.stdout)
        self.assertIn('intake の鍵（ctl.env） CLAUDE_CODE_OAUTH_TOKEN: dummy-to…0001', r.stdout)
        self.assertIn('発行から 0 日（issued ', r.stdout)
        self.assertNotIn(TOKEN, r.stdout)
        self.assertIn('pool: 0 本', r.stdout)                             # keys.json が無い

    def test_show_warns_about_claude_lines_left_in_env_files(self):
        """ADR-0006 の運用で書いた CLAUDE_CODE_OAUTH_TOKEN= の行が残っていれば、使われないことと消し方を言う（値は出さない）"""
        pathlib.Path(self.env).write_text('SB_DOMAIN=t.sb.internal\nCLAUDE_CODE_OAUTH_TOKEN=stale-global-0001\n')
        self.write_pj('alpha', 'GH_REPO=o/alpha\nCLAUDE_CODE_OAUTH_TOKEN_FABLE=stale-alpha-0002\n')
        r = self.run_sandbox(['show'])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('[stale]', r.stdout)
        self.assertIn('%s: CLAUDE_CODE_OAUTH_TOKEN\n' % self.env, r.stdout)
        self.assertIn('%s: CLAUDE_CODE_OAUTH_TOKEN_FABLE\n' % os.path.join(self.pj_dir, 'alpha.env'), r.stdout)
        self.assertNotIn('stale-global', r.stdout); self.assertNotIn('stale-alpha', r.stdout)
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN: stale', r.stdout)      # env の鍵を「効いている鍵」として出さない
        r = self.run_sandbox(['show'], ctl_env=os.path.join(self.dir, 'no-such-ctl.env'))
        self.assertIn('[stale]', r.stdout)

    def test_show_is_quiet_when_no_claude_line_is_left(self):
        r = self.run_sandbox(['show', 'alpha'])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('[stale]', r.stdout)
        self.assertIn('GH_TOKEN:', r.stdout)
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN:', r.stdout.replace('intake の鍵（ctl.env） CLAUDE_CODE_OAUTH_TOKEN:', ''))   # env の鍵の行は無い

    def test_show_tells_a_non_control_host_to_use_the_control_plane(self):
        r = self.run_sandbox(['show'], ctl_env=os.path.join(self.dir, 'no-such-ctl.env'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('host: 手元（ctl.env なし）', r.stdout)
        self.assertNotIn('intake の鍵', r.stdout)


if __name__ == '__main__':
    unittest.main()
