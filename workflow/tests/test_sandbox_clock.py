"""貸出（take / reset）が、ゲストの時計を制御系に合わせてから env を注入すること（チケット 491）。

  python3 -m unittest discover -s workflow/tests -v

プール VM は RAM 込みの snapshot を `qm rollback` で戻すので、復元直後のゲストの時計は snapshot 取得時刻から
再開する。timesyncd の次のポーリング（最大 34 分）まで数日ずれたまま走り、その間に付いたコミットの author date と
agent が書く ADR の日付が嘘になる。だから貸出のたびに合わせる。

Proxmox と ssh を叩く関数は偽装し、sandbox/bin/sandbox の該当区画だけを切り出して bash で走らせる
（`test_sandbox_take.py` と同じ流儀）。偽の `vm` は $OFFSET ファイルの秒数だけずれた epoch を返し、
timesyncd の再起動と `date -u -s` でその値が変わるようにしてある。
"""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / 'sandbox/bin/sandbox'
POOL_KEY = '{"keys":[{"name":"k","token":"fake-token-k","allow":{"fable":true,"other":true},"enabled":true}]}'

# 偽ゲスト: $OFFSET（秒）だけずれた epoch を返す。timesyncd の再起動は $CONVERGE のときだけ効き、
# `date -u -s @N` は必ず効く（sudo が通る VM 前提）。呼ばれたコマンドは $CALLS に残す
FAKE_VM = '''
vm() { local ip=$1; shift
  printf '%s\\n' "$*" >> "$CALLS"
  case "$*" in
    *"date -u +%s"*) echo $(( $(date -u +%s) + $(cat "$OFFSET") )) ;;
    *"date -u -s @"*) local want="${*##*@}"; want="${want%\\'*}"; echo $(( want - $(date -u +%s) )) > "$OFFSET" ;;
    *timesyncd*) [[ -n "${CONVERGE:-}" ]] && echo 0 > "$OFFSET" ;;
  esac
  return 0
}
'''


@unittest.skipUnless(shutil.which('jq'), 'jq required by sandbox CLI')
class SandboxClockTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.dir = d.name
        self.state = os.path.join(self.dir, 'state.json')
        pathlib.Path(self.state).write_text('{}')
        pathlib.Path(self.dir, 'keys.json').write_text(POOL_KEY)
        self.offset = os.path.join(self.dir, 'offset')
        self.calls = os.path.join(self.dir, 'calls')
        pathlib.Path(self.calls).write_text('')

    def script(self, fakes, tail):
        text = SCRIPT.read_text()
        body = text[text.index('# ---------- state（台帳とロック）'):text.index('mask() {')]
        head = ('set -euo pipefail\n'
                'CONF_DIR=%s\nSTATE=%s\nSB_DOMAIN=t.sb.internal\nAPP_PORT=3000\n'
                'die() { echo "[error] $*" >&2; exit 1; }\n' % (self.dir, self.state))
        return head + body + fakes + tail

    def sync(self, offset, converge=False, wait_s='2', tolerance_s='120'):
        """sync_clock を 1 回走らせる。戻り: (rc, stdout, stderr)"""
        pathlib.Path(self.offset).write_text(str(offset) + '\n')
        env = dict(os.environ, OFFSET=self.offset, CALLS=self.calls,
                   SB_CLOCK_WAIT_S=wait_s, SB_CLOCK_TOLERANCE_S=tolerance_s)
        if converge: env['CONVERGE'] = '1'
        p = subprocess.run(['bash', '-c', self.script(FAKE_VM, 'sync_clock 10.77.1.1\n')],
                           text=True, capture_output=True, env=env)
        return p.returncode, p.stdout, p.stderr

    def calls_text(self):
        return pathlib.Path(self.calls).read_text()

    def guest_offset(self):
        return int(pathlib.Path(self.offset).read_text().strip())

    # ---------- a: 合っているゲストには何もしない（測って記録に残すだけ）
    def test_a_guest_already_in_sync_is_measured_and_left_alone(self):
        rc, out, err = self.sync(0)
        self.assertEqual(rc, 0, out + err)
        self.assertIn('[clock]', out)
        self.assertIn('0s', out)
        self.assertNotIn('timesyncd', self.calls_text())     # 直す必要が無いものを触らない
        self.assertNotIn('date -u -s', self.calls_text())

    # ---------- b: ずれていたら timesyncd を起こし直し、収束を待つ
    def test_a_skewed_guest_is_resynced_by_restarting_timesyncd(self):
        rc, out, err = self.sync(578400, converge=True)
        self.assertEqual(rc, 0, out + err)
        self.assertIn('timesyncd', self.calls_text())
        self.assertNotIn('date -u -s', self.calls_text())    # NTP で収束したなら手で入れない
        self.assertIn('578400s', out)                        # 前のずれが読める
        self.assertRegex(out, r'→ -?[01]s')                  # 後のずれも読める（測り直しの 1 秒はぶれる）
        self.assertEqual(self.guest_offset(), 0)
        self.assertNotIn('[warn]', out + err)

    # ---------- c: NTP が届かない網なら制御系の時刻を手で入れる
    def test_a_guest_that_does_not_converge_is_set_from_the_control_clock(self):
        rc, out, err = self.sync(578400, converge=False)
        self.assertEqual(rc, 0, out + err)
        self.assertIn('date -u -s', self.calls_text())
        self.assertLessEqual(abs(self.guest_offset()), 2)
        self.assertRegex(out, r'→ -?[01]s')
        self.assertNotIn('[warn]', out + err)

    # ---------- d: それでも直らないときは警告だけ出して続ける（止めるのは runner の役目）
    def test_a_guest_that_cannot_be_fixed_warns_but_does_not_fail_the_take(self):
        pathlib.Path(self.offset).write_text('578400\n')
        env = dict(os.environ, OFFSET=self.offset, CALLS=self.calls, SB_CLOCK_WAIT_S='1', SB_CLOCK_TOLERANCE_S='120')
        # date -u -s も効かないゲスト（sudo が無い等）
        stubborn = FAKE_VM.replace('*"date -u -s @"*) local want="${*##*@}"; want="${want%\\\'*}"; echo $(( want - $(date -u +%s) )) > "$OFFSET" ;;',
                                   '*"date -u -s @"*) return 1 ;;')
        p = subprocess.run(['bash', '-c', self.script(stubborn, 'sync_clock 10.77.1.1\n')],
                           text=True, capture_output=True, env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)   # take 自体は止めない
        self.assertIn('[warn]', p.stdout + p.stderr)
        self.assertIn('578400', p.stdout + p.stderr)

    # ---------- e: take と reset は必ずこれを通る（巻き戻しの有無に関わらず）
    def test_take_and_reset_sync_the_clock_before_injecting_env(self):
        fakes = ('''
load_pj() { CUR_PJ=$1; }
pool_list() { echo "9201 sb-t-pj-01 10.77.1.1 running"; }
pve_has_clean() { return 0; }
rollback() { echo "rollback" >> "$CALLS"; return 0; }
wait_port() { return 0; }
sync_clock() { echo "sync_clock $1" >> "$CALLS"; }
inject_env() { echo "inject_env" >> "$CALLS"; return 0; }
dns_set() { return 0; }
dns_del() { return 0; }
touch_used() { return 0; }
''')
        env = dict(os.environ, CALLS=self.calls)
        take = subprocess.run(['bash', '-c', self.script(fakes, 'cmd_take "$@"\n'), 'sandbox', 'pj', '101'],
                              text=True, capture_output=True, env=env)
        self.assertEqual(take.returncode, 0, take.stdout + take.stderr)
        calls = [l for l in self.calls_text().splitlines() if l]
        self.assertIn('sync_clock 10.77.1.1', calls)
        # 時計を合わせてから env を書く（順序が逆だと GH_TOKEN_EXPIRES_AT が嘘の時刻で入る）
        self.assertLess(calls.index('sync_clock 10.77.1.1'), calls.index('inject_env'))
        pathlib.Path(self.calls).write_text('')
        reset = subprocess.run(['bash', '-c', self.script(fakes, 'cmd_reset "$@"\n'), 'sandbox', '101'],
                               text=True, capture_output=True, env=env)
        self.assertEqual(reset.returncode, 0, reset.stdout + reset.stderr)
        calls = [l for l in self.calls_text().splitlines() if l]
        self.assertIn('sync_clock 10.77.1.1', calls)         # 巻き戻した直後こそずれている
        self.assertLess(calls.index('sync_clock 10.77.1.1'), calls.index('inject_env'))


if __name__ == '__main__':
    unittest.main()
