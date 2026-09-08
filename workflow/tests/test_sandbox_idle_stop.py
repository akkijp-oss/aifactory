"""使われていないプール VM を止める `sandbox idle-stop`（252）。

sandbox/bin/sandbox から state の区画と idle-stop の区画だけを切り出し、Proxmox を叩く関数を偽装して bash で走らせる。
止める / 起動する API（pve_shutdown / pve_stop / pve_start）は呼ばれた順に $CALLS へ書くだけにする。

- 貸出中（state.json に vmid がある）VM は止めない
- 最終利用（last-used.json）から N 時間経っていなければ止めない。超えていれば shutdown
- 記録が無ければ uptime で代用し、uptime も取れなければ止めない（安全側）
- --dry-run は止めず idle-stop.json も書かない / SB_IDLE_STOP_HOURS=0 は何もしない
- ctl / gw / テンプレート / 別テナントの VM は対象外
- shutdown が 120 秒で止まらなければ stop
- 止めた VM は idle-stop.json に残り、`sandbox ls` が脚注で「節電で停止中」と言う
- 停止中の VM を掴んだ rollback() は起動して、その旨をログに出す（take の待ちが伸びる理由）
"""
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / 'sandbox/bin/sandbox'

# プールは 2 台 + 対象外の 4 台（LXC の ctl / gw、テンプレート、別テナント）。
# pve_uptime は UPTIME（空なら「不明」）、止める API は $CALLS に記録して SHUTDOWN_RC を返す
FAKES = '''
SB_PREFIX=sb-t
load_pj() { CUR_PJ=$1; }
pve_vms() {
  echo "9201 sb-t-pj-01 running"
  echo "9202 sb-t-pj-02 running"
  echo "9000 sb-t-gw running"
  echo "9001 sb-t-ctl running"
  echo "9110 sb-t-tpl-pj running"
  echo "9301 sb-other-pj-01 running"
}
pve_uptime() { echo "${UPTIME:-}"; }
pve_shutdown() { echo "shutdown $1" >> "$CALLS"; return "${SHUTDOWN_RC:-0}"; }
pve_stop() { echo "stop $1" >> "$CALLS"; return "${STOP_RC:-0}"; }
'''

LENT = ('{"101":{"vmid":9201,"name":"sb-t-pj-01","ip":"10.77.1.1","pj":"pj",'
        '"since":"2026-09-06T10:00:00+09:00"}}')


def iso(hours_ago):
    return (datetime.datetime.now().astimezone()
            - datetime.timedelta(hours=hours_ago)).isoformat(timespec='seconds')


@unittest.skipUnless(shutil.which('jq'), 'jq required by sandbox CLI')
class SandboxIdleStopTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.state = os.path.join(self.dir, 'state.json')
        self.last_used = os.path.join(self.dir, 'last-used.json')
        self.idle_file = os.path.join(self.dir, 'idle-stop.json')
        self.calls_file = os.path.join(self.dir, 'calls')
        pathlib.Path(self.state).write_text('{}')

    def script(self, fakes=FAKES, tail='cmd_idle_stop "$@"\n'):
        text = SCRIPT.read_text()
        state = text[text.index('# ---------- state（台帳とロック）'):text.index('mask() {')]
        idle = text[text.index('# ---------- idle-stop'):text.index('cmd_ls() {')]
        head = ('set -euo pipefail\n'
                'CONF_DIR=%s\nSTATE=%s\n'
                'die() { echo "[error] $*" >&2; exit 1; }\n' % (self.dir, self.state))
        return head + state + idle + fakes + tail

    def run_idle_stop(self, *args, fakes=FAKES, **env):
        script = self.script(fakes)
        return subprocess.run(['bash', '-c', script, 'sandbox', *args], text=True,
                              env=dict(os.environ, CALLS=self.calls_file, **env),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def calls(self):
        p = pathlib.Path(self.calls_file)
        return p.read_text().split() if p.exists() else []

    def write_last_used(self, **by_vmid):
        pathlib.Path(self.last_used).write_text(json.dumps({k.lstrip('v'): v for k, v in by_vmid.items()}))

    def idle_json(self):
        return json.loads(pathlib.Path(self.idle_file).read_text())

    # ---------- 止める / 止めない

    def test_lent_vm_is_never_stopped(self):
        """貸出中（台帳に vmid がある）VM は、どれだけ古くても止めない"""
        pathlib.Path(self.state).write_text(LENT)
        self.write_last_used(v9201=iso(99), v9202=iso(99))
        r = self.run_idle_stop()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ['shutdown', '9202'])       # 9201 は貸出中
        self.assertIn('貸出中 1', r.stdout)

    def test_recent_use_is_kept_and_old_use_is_stopped(self):
        """最終利用から 2 時間なら止めず、4 時間なら止める（既定 3 時間）"""
        self.write_last_used(v9201=iso(2), v9202=iso(4))
        r = self.run_idle_stop()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ['shutdown', '9202'])
        self.assertIn('sb-t-pj-02 (9202): shutdown', r.stdout)
        self.assertIn('最終利用 4h00m 前', r.stdout)
        self.assertIn('対象 2 台: 停止 1 / 貸出中 0 / 未経過 1 / 不明 0', r.stdout)

    def test_hours_option_overrides_the_default(self):
        self.write_last_used(v9201=iso(2), v9202=iso(4))
        self.assertEqual(self.run_idle_stop('--hours', '5').returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.run_idle_stop('--hours', '1').returncode, 0)
        self.assertEqual(self.calls(), ['shutdown', '9201', 'shutdown', '9202'])

    def test_uptime_stands_in_when_there_is_no_record(self):
        """last-used.json に記録が無い VM は「起動からの時間」で判断する"""
        r = self.run_idle_stop(UPTIME=str(4 * 3600))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ['shutdown', '9201', 'shutdown', '9202'])
        self.assertIn('起動から 4h00m 前', r.stdout)

    def test_unknown_age_is_left_running(self):
        """記録も uptime も無ければ止めない（安全側）。理由を 1 行出す"""
        r = self.run_idle_stop(UPTIME='')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), [])
        self.assertIn('最終利用の記録なし・uptime も不明', r.stdout)
        self.assertIn('不明 2', r.stdout)

    def test_dry_run_touches_nothing(self):
        self.write_last_used(v9201=iso(4), v9202=iso(4))
        r = self.run_idle_stop('--dry-run')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), [])
        self.assertFalse(os.path.exists(self.idle_file))
        self.assertIn('--dry-run なので止めない', r.stdout)
        self.assertIn('停止 2', r.stdout)

    def test_zero_hours_disables_idle_stop(self):
        self.write_last_used(v9201=iso(99), v9202=iso(99))
        r = self.run_idle_stop(SB_IDLE_STOP_HOURS='0')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), [])
        self.assertFalse(os.path.exists(self.idle_file))
        self.assertIn('無効', r.stdout)

    def test_per_pj_hours_can_keep_a_project_always_on(self):
        """PJ 別の SB_IDLE_STOP_HOURS=0 は、その PJ の VM だけ対象から外す（他の PJ に漏れない）"""
        fakes = FAKES.replace('load_pj() { CUR_PJ=$1; }',
                              'load_pj() { CUR_PJ=$1; SB_IDLE_STOP_HOURS=0; }')
        self.write_last_used(v9201=iso(99), v9202=iso(99))
        r = self.run_idle_stop(fakes=fakes)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), [])
        self.assertIn('対象外 2', r.stdout)

    def test_bad_hours_is_refused(self):
        r = self.run_idle_stop('--hours', 'three')
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn('0 以上の整数', r.stderr)

    # ---------- 対象の絞り込み

    def test_only_pool_vms_of_this_tenant_are_considered(self):
        """LXC の ctl / gw・テンプレート・別テナントの VM は数にも入らない"""
        r = self.run_idle_stop(UPTIME=str(99 * 3600))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ['shutdown', '9201', 'shutdown', '9202'])
        self.assertIn('対象 2 台', r.stdout)
        for name in ('sb-t-gw', 'sb-t-ctl', 'sb-t-tpl-pj', 'sb-other-pj-01'):
            self.assertNotIn(name, r.stdout)

    def test_already_stopped_vms_are_not_counted(self):
        fakes = FAKES.replace('echo "9202 sb-t-pj-02 running"', 'echo "9202 sb-t-pj-02 stopped"')
        r = self.run_idle_stop(UPTIME=str(4 * 3600), fakes=fakes)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ['shutdown', '9201'])
        self.assertIn('対象 1 台', r.stdout)

    # ---------- shutdown が効かないとき

    def test_stop_is_used_when_shutdown_times_out(self):
        self.write_last_used(v9201=iso(4), v9202=iso(1))
        r = self.run_idle_stop(SHUTDOWN_RC='1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ['shutdown', '9201', 'stop', '9201'])
        self.assertIn('shutdown が 120 秒で止まらず stop', r.stderr)
        self.assertEqual([v['vmid'] for v in self.idle_json()['stopped']], [9201])

    def test_a_vm_that_refuses_both_is_reported_and_not_recorded(self):
        self.write_last_used(v9201=iso(4), v9202=iso(1))
        r = self.run_idle_stop(SHUTDOWN_RC='1', STOP_RC='1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('stop も失敗', r.stderr)
        self.assertIn('失敗 1', r.stdout)
        self.assertEqual(self.idle_json()['stopped'], [])

    # ---------- idle-stop.json と ls の脚注

    def test_result_file_records_hours_last_run_and_stopped(self):
        used = iso(4)
        self.write_last_used(v9201=used, v9202=iso(1))
        self.assertEqual(self.run_idle_stop().returncode, 0)
        d = self.idle_json()
        self.assertEqual(d['hours'], 3)
        self.assertRegex(d['last_run'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$')   # ADR-0026
        self.assertEqual(len(d['stopped']), 1)
        self.assertEqual(d['stopped'][0]['vmid'], 9201)
        self.assertEqual(d['stopped'][0]['name'], 'sb-t-pj-01')
        self.assertEqual(d['stopped'][0]['last_used'], used)

    def test_previously_stopped_vms_stay_listed_until_they_come_back_up(self):
        """前回止めた VM は、まだ止まっている間だけ一覧に残る（take が起動したら次の実行で消える）"""
        pathlib.Path(self.idle_file).write_text(json.dumps(
            {"hours": 3, "last_run": iso(1),
             "stopped": [{"vmid": 9201, "name": "sb-t-pj-01", "at": iso(1), "last_used": None},
                         {"vmid": 9202, "name": "sb-t-pj-02", "at": iso(1), "last_used": None}]}))
        # 9201 は take で起動されて running に戻り、9202 は止まったまま
        fakes = FAKES.replace('echo "9202 sb-t-pj-02 running"', 'echo "9202 sb-t-pj-02 stopped"')
        self.write_last_used(v9201=iso(1))
        self.assertEqual(self.run_idle_stop(fakes=fakes).returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual([v['vmid'] for v in self.idle_json()['stopped']], [9202])

    def test_ls_footnote_explains_the_stopped_vms(self):
        """`sandbox ls` の表の後に「節電で停止中」を 1 行足す（列は増やさない）"""
        text = SCRIPT.read_text()
        ls = text[text.index('cmd_ls() {'):text.index('case "${1:-}" in')]
        head = ('set -euo pipefail\nSTATE=%s\nSB_PREFIX=sb-t\nCONF_DIR=%s\n'
                'die() { echo "[error] $*" >&2; exit 1; }\n' % (self.state, self.dir))
        state = text[text.index('# ---------- state（台帳とロック）'):text.index('mask() {')]
        pathlib.Path(self.idle_file).write_text(json.dumps(
            {"hours": 3, "last_run": "2026-09-08T04:00:00+09:00",
             "stopped": [{"vmid": 9202, "name": "sb-t-pj-02", "at": "2026-09-08T04:00:00+09:00", "last_used": None}]}))
        script = (head + state + 'pve_vms() { echo "9201 sb-t-pj-01 running"; }\npool_ip() { echo 10.77.1.1; }\n'
                  + ls + 'cmd_ls\n')
        r = subprocess.run(['bash', '-c', script], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('[idle-stop] 1 台が節電で停止中', r.stdout)
        self.assertIn('2026-09-08T04:00:00+09:00', r.stdout)
        rows = [l for l in r.stdout.splitlines() if l and not l.startswith(('TASK', '['))]
        self.assertEqual(len(rows), 1, r.stdout)       # 脚注は `[` 始まりで、console の parse_ls が捨てる


# rollback() が停止中の VM を掴んだときの経路（idle-stop で止まった VM を take が起動し直す）
ROLLBACK_FAKES = '''
pve_lock() { :; }
pve_rollback() { return 0; }
pve_status() { echo "${VM_STATUS:-running}"; }
pve_start() { echo "start $1" >> "$CALLS"; return 0; }
wait_port() { return 0; }
'''


@unittest.skipUnless(shutil.which('jq'), 'jq required by sandbox CLI')
class SandboxStartMessageTest(unittest.TestCase):
    """停止中の VM を take すると起動して待つ。待った理由が runner のログに残ること（252）"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.state = os.path.join(self.dir, 'state.json')
        self.calls_file = os.path.join(self.dir, 'calls')
        pathlib.Path(self.state).write_text('{}')

    script = SandboxIdleStopTest.script

    def rollback(self, status):
        text = SCRIPT.read_text()
        state = text[text.index('# ---------- state（台帳とロック）'):text.index('mask() {')]
        head = ('set -euo pipefail\nCONF_DIR=%s\nSTATE=%s\nSB_ROLLBACK_TRIES=1\nSB_ROLLBACK_WAIT=0\n'
                'die() { echo "[error] $*" >&2; exit 1; }\n' % (self.dir, self.state))
        script = head + state + ROLLBACK_FAKES + 'rollback 9201 10.77.1.1\n'
        return subprocess.run(['bash', '-c', script], text=True,
                              env=dict(os.environ, CALLS=self.calls_file, VM_STATUS=status),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_stopped_vm_is_started_and_says_so(self):
        r = self.rollback('stopped')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('start 9201', pathlib.Path(self.calls_file).read_text())
        self.assertRegex(r.stdout, r'\[start\] vm 9201: 停止中だったので起動した（\d+ 秒）')

    def test_running_vm_says_nothing(self):
        r = self.rollback('running')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.calls_file))
        self.assertNotIn('[start]', r.stdout)


@unittest.skipUnless(shutil.which('jq'), 'jq required by sandbox CLI')
class SandboxLastUsedTest(unittest.TestCase):
    """take / reset / release / reinject が最終利用を記録すること（記録が無ければ idle-stop は uptime 頼みになる）"""

    FAKES = '''
load_pj() { CUR_PJ=$1; CLAUDE_CODE_OAUTH_TOKEN=dummy; }
pool_list() { echo "9201 sb-t-pj-01 10.77.1.1 running"; }
pve_has_clean() { return 0; }
rollback() { return 0; }
wait_port() { return 0; }
inject_env() { return 0; }
dns_set() { return 0; }
dns_del() { return 0; }
'''

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.state = os.path.join(self.dir, 'state.json')
        self.last_used = os.path.join(self.dir, 'last-used.json')
        pathlib.Path(self.state).write_text('{}')

    def run_cmd(self, cmd, *args):
        text = SCRIPT.read_text()
        body = text[text.index('# ---------- state（台帳とロック）'):text.index('mask() {')]
        head = ('set -euo pipefail\nCONF_DIR=%s\nSTATE=%s\nSB_DOMAIN=t.sb.internal\nAPP_PORT=3000\n'
                'die() { echo "[error] $*" >&2; exit 1; }\n' % (self.dir, self.state))
        return subprocess.run(['bash', '-c', head + body + self.FAKES + 'cmd_%s "$@"\n' % cmd, 'sandbox', *args],
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def last(self):
        return json.loads(pathlib.Path(self.last_used).read_text())

    def test_take_records_the_vm_as_used_now(self):
        r = self.run_cmd('take', 'pj', '101')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(list(self.last()), ['9201'])
        self.assertRegex(self.last()['9201'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$')

    def test_release_records_the_vm_so_it_is_not_stopped_at_once(self):
        """返却直後の VM は「今使った」ことにする（3 時間は起動したまま残す）"""
        pathlib.Path(self.state).write_text(
            '{"101":{"vmid":9201,"name":"sb-t-pj-01","ip":"10.77.1.1","pj":"pj","since":"x"}}')
        r = self.run_cmd('release', '101')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(list(self.last()), ['9201'])

    def test_reset_records_the_vm(self):
        pathlib.Path(self.state).write_text(
            '{"101":{"vmid":9201,"name":"sb-t-pj-01","ip":"10.77.1.1","pj":"pj","since":"x"}}')
        r = self.run_cmd('reset', '101')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(list(self.last()), ['9201'])
