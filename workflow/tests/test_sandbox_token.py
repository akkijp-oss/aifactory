"""sandbox token rotate / show（246）: 1 回の入力で global・全 PJ・ctl.env が差し替わること。

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
pool_apply_keys() { :; }   # 鍵プール（379）。切り出しの外にあるので空にする（rotate は env の鍵だけを見る）
state_set_keys() { :; }
ghapp_ready() { return 1; }
inject_env() { echo "$2" >> "$CALLS"; return 0; }
systemctl() { echo "systemctl $*" >> "$CALLS"; [[ "$1" == list-unit-files ]] && echo "aifactory-console.service enabled"; return 0; }
sudo() { if [[ "$1" == -n ]]; then return "$SUDO_RC"; fi; echo "sudo $*" >> "$CALLS"; "$@"; }
'''


@unittest.skipUnless(shutil.which('jq'), 'jq required by sandbox CLI')
class SandboxTokenTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = os.path.join(self.dir, 'env')
        self.pj_dir = os.path.join(self.dir, 'pj')
        self.ctl = os.path.join(self.dir, 'ctl.env')
        self.state = os.path.join(self.dir, 'state.json')
        self.calls = os.path.join(self.dir, 'calls')
        os.mkdir(self.pj_dir)
        pathlib.Path(self.env).write_text('SB_DOMAIN=t.sb.internal\n')
        pathlib.Path(self.state).write_text('{}')
        self.write_pj('alpha', 'GH_REPO=o/alpha\nCLAUDE_CODE_OAUTH_TOKEN=old-alpha\n')
        self.write_pj('bravo', 'GH_REPO=o/bravo\nCLAUDE_CODE_OAUTH_TOKEN=old-bravo\n')
        self.write_pj('charlie', 'GH_REPO=o/charlie\n')          # 鍵を持たない PJ
        self.write_ctl()

    def write_pj(self, pj, body):
        pathlib.Path(self.pj_dir, pj + '.env').write_text('# sandbox PJ 別設定: %s\n%s' % (pj, body))

    def write_ctl(self):
        pathlib.Path(self.ctl).write_text(
            '# 制御系のプロセスに渡す環境\nCONSOLE_TOKEN=console-secret\nCONSOLE_PORT=8765\n'
            '# intake が claude -p を呼ぶ用\nCLAUDE_CODE_OAUTH_TOKEN=old-ctl\nGH_TOKEN=\n')

    def script(self, src_claude='global(env)', extra=''):
        text = SCRIPT.read_text()
        body = text[text.index('mask() {'):text.index('cmd_gh_app() {')]
        head = ('set -euo pipefail\n'
                'ENV_FILE=%s\nPJ_DIR=%s\nSTATE=%s\nCTL_ENV=${SANDBOX_CTL_ENV:-%s}\n'
                'SB_DOMAIN=t.sb.internal\nAPP_PORT=3000\nAPI_MODE=0\nPVE_HOST=x\n'
                '_src_claude="%s"\n_src_gh=\n'
                'die() { echo "[error] $*" >&2; exit 1; }\n'
                % (self.env, self.pj_dir, self.state, self.ctl, src_claude))
        return head + body + FAKES + extra

    def run_sandbox(self, args, stdin='', sudo_rc='0', ctl_env=None, src_claude='global(env)'):
        env = dict(os.environ, CALLS=self.calls, SUDO_RC=sudo_rc)
        if ctl_env is not None:
            env['SANDBOX_CTL_ENV'] = ctl_env
        script = self.script(src_claude) + 'cmd_token "$@"\n'
        return subprocess.run(['bash', '-c', script, 'sandbox'] + args, input=stdin, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

    def read(self, path):
        return pathlib.Path(path).read_text()

    def calls_text(self):
        return self.read(self.calls) if os.path.exists(self.calls) else ''

    # ---------- rotate

    def test_rotate_updates_global_and_every_pj_holding_the_key(self):
        r = self.run_sandbox(['rotate'], stdin=TOKEN)
        self.assertEqual(r.returncode, 0, r.stderr)
        for f in (self.env, os.path.join(self.pj_dir, 'alpha.env'), os.path.join(self.pj_dir, 'bravo.env')):
            self.assertIn('CLAUDE_CODE_OAUTH_TOKEN=%s\n' % TOKEN, self.read(f), f)
            self.assertIn('[updated] %s' % f, r.stdout)
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', self.read(os.path.join(self.pj_dir, 'charlie.env')))
        self.assertIn('[skip]', r.stdout)
        self.assertIn('4 箇所更新 / 1 箇所 skip', r.stdout)     # global + alpha + bravo + ctl.env
        self.assertNotIn('old-alpha', self.read(os.path.join(self.pj_dir, 'alpha.env')))
        self.assertNotIn(TOKEN, r.stdout)                       # 生のトークンは出さない（マスクのみ）

    def test_rotate_replaces_only_the_key_line_in_ctl_env(self):
        r = self.run_sandbox(['rotate'], stdin=TOKEN)
        self.assertEqual(r.returncode, 0, r.stderr)
        ctl = self.read(self.ctl)
        self.assertIn('CONSOLE_TOKEN=console-secret', ctl)      # 同居する鍵を消さない
        self.assertIn('CONSOLE_PORT=8765', ctl)
        self.assertIn('# 制御系のプロセスに渡す環境', ctl)       # コメントも残す
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN=%s' % TOKEN, ctl)
        self.assertNotIn('old-ctl', ctl)

    def test_rotate_records_one_issued_comment_per_file(self):
        self.assertEqual(self.run_sandbox(['rotate'], stdin=TOKEN).returncode, 0)
        self.assertEqual(self.run_sandbox(['rotate'], stdin=TOKEN2).returncode, 0)
        for f in (self.env, self.ctl, os.path.join(self.pj_dir, 'alpha.env')):
            lines = [l for l in self.read(f).splitlines() if l.startswith('# CLAUDE_CODE_OAUTH_TOKEN issued: ')]
            self.assertEqual(len(lines), 1, self.read(f))
            self.assertRegex(lines[0], r'issued: \d{4}-\d{2}-\d{2}$')
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN=%s\n' % TOKEN2, self.read(self.ctl))

    def test_rotate_restarts_console_and_reinjects_every_lent_task(self):
        pathlib.Path(self.state).write_text(
            '{"101":{"vmid":9201,"ip":"10.77.1.1","pj":"alpha"},'
            ' "102":{"vmid":9202,"ip":"10.77.1.2","pj":"bravo"}}')
        r = self.run_sandbox(['rotate'], stdin=TOKEN)
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.calls_text()
        self.assertIn('systemctl restart aifactory-console', calls)
        self.assertIn('[ok] aifactory-console を restart した', r.stdout)
        self.assertEqual(sorted(l for l in calls.splitlines() if l in ('101', '102')), ['101', '102'])

    def test_rotate_prints_the_command_when_passwordless_sudo_is_unavailable(self):
        r = self.run_sandbox(['rotate'], stdin=TOKEN, sudo_rc='1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('sudo systemctl restart aifactory-console', r.stdout)
        self.assertNotIn('systemctl restart', self.calls_text())
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN=%s' % TOKEN, self.read(self.ctl))   # 更新自体は済んでいる

    def test_rotate_warns_but_continues_when_the_host_has_no_ctl_env(self):
        missing = os.path.join(self.dir, 'no-such-ctl.env')
        r = self.run_sandbox(['rotate'], stdin=TOKEN, ctl_env=missing)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('[warn]', r.stdout)
        self.assertIn('制御系 LXC', r.stdout)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN=%s\n' % TOKEN, self.read(self.env))
        self.assertNotIn('systemctl restart', self.calls_text())
        self.assertFalse(os.path.exists(missing))

    def test_rotate_refuses_an_empty_token(self):
        r = self.run_sandbox(['rotate'], stdin='\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('空のトークン', r.stderr)
        self.assertIn('old-ctl', self.read(self.ctl))

    def test_rotate_rejects_an_unknown_kind(self):
        r = self.run_sandbox(['rotate', 'slack'], stdin=TOKEN)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('claude か gh', r.stderr)

    def test_rotate_gh_touches_only_the_gh_key(self):
        self.write_pj('alpha', 'GH_REPO=o/alpha\nCLAUDE_CODE_OAUTH_TOKEN=old-alpha\nGH_TOKEN=old-gh\n')
        r = self.run_sandbox(['rotate', 'gh'], stdin=TOKEN)
        self.assertEqual(r.returncode, 0, r.stderr)
        alpha = self.read(os.path.join(self.pj_dir, 'alpha.env'))
        self.assertIn('GH_TOKEN=%s\n' % TOKEN, alpha)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN=old-alpha', alpha)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN=old-ctl', self.read(self.ctl))

    # ---------- set

    def test_set_for_a_project_without_a_lent_task_exits_zero(self):
        """貸出中の task が無いと [hint] の && が偽になり、set が終了コード 1 を返していた"""
        r = self.run_sandbox(['set', 'alpha'], stdin=TOKEN)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_set_twice_keeps_a_single_issued_line(self):
        self.assertEqual(self.run_sandbox(['set', 'alpha'], stdin=TOKEN).returncode, 0)
        self.assertEqual(self.run_sandbox(['set', 'alpha'], stdin=TOKEN2).returncode, 0)
        body = self.read(os.path.join(self.pj_dir, 'alpha.env'))
        self.assertEqual(body.count('# CLAUDE_CODE_OAUTH_TOKEN issued: '), 1, body)
        self.assertEqual(body.count('CLAUDE_CODE_OAUTH_TOKEN='), 1, body)   # 値の行は 1 本だけ
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN=%s\n' % TOKEN2, body)
        self.assertIn('GH_REPO=o/alpha', body)

    def test_clear_removes_the_value_and_the_issued_line(self):
        self.assertEqual(self.run_sandbox(['set', 'alpha'], stdin=TOKEN).returncode, 0)
        self.assertEqual(self.run_sandbox(['clear', 'alpha'], stdin='').returncode, 0)
        body = self.read(os.path.join(self.pj_dir, 'alpha.env'))
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', body)
        self.assertIn('GH_REPO=o/alpha', body)

    # ---------- show

    def test_show_reports_the_host_kind_and_the_age_of_the_token(self):
        self.assertEqual(self.run_sandbox(['rotate'], stdin=TOKEN).returncode, 0)
        r = self.run_sandbox(['show'], src_claude='global(env)')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('host: 制御系（ctl.env あり）', r.stdout)
        self.assertIn('発行から 0 日（issued ', r.stdout)

    def test_show_says_unknown_when_the_file_has_no_issued_comment(self):
        pathlib.Path(self.env).write_text('SB_DOMAIN=t.sb.internal\nCLAUDE_CODE_OAUTH_TOKEN=old\n')
        r = self.run_sandbox(['show'], src_claude='global(env)')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('発行日不明', r.stdout)

    def test_show_tells_a_non_control_host_to_use_the_control_plane(self):
        r = self.run_sandbox(['show'], ctl_env=os.path.join(self.dir, 'no-such-ctl.env'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('host: 手元（ctl.env なし）', r.stdout)
