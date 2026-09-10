"""Regression: 同時に走る sandbox take が同じ VM を二重に貸し出さないこと（234）。

Proxmox を呼ぶ関数は偽装し、sandbox/bin/sandbox から state / take の区画だけを切り出して bash で走らせる。
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / 'sandbox/bin/sandbox'
POOL_KEY = '{"keys":[{"name":"k","token":"fake-token-k","allow":{"fable":true,"other":true},"enabled":true}]}'   # take が鍵プールから選ぶ 1 本（ADR-0060。env の鍵は無い）

# Proxmox / ssh / DNS を叩く関数を空にし、プールは 2 台に固定する。
# pve_has_clean の sleep で「選定 → 予約」の窓を広げ、ロックが無ければ必ず衝突するようにする
FAKES = '''
load_pj() { CUR_PJ=$1; }
pool_list() { echo "9201 sb-t-pj-01 10.77.1.1 stopped"; echo "9202 sb-t-pj-02 10.77.1.2 stopped"; }
pve_has_clean() { sleep 0.5; return 0; }
rollback() { return 0; }
wait_port() { return 0; }
inject_env() { return 0; }
dns_set() { return 0; }
dns_del() { return 0; }
'''


@unittest.skipUnless(shutil.which('jq'), 'jq required by sandbox CLI')
class SandboxTakeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state = os.path.join(self.dir, 'state.json')
        pathlib.Path(self.state).write_text('{}')
        pathlib.Path(self.dir, 'keys.json').write_text(POOL_KEY)

    def script(self, fakes=FAKES, tail=''):
        text = SCRIPT.read_text()
        body = text[text.index('# ---------- state（台帳とロック）'):text.index('mask() {')]
        head = ('set -euo pipefail\n'
                'CONF_DIR=%s\nSTATE=%s\nSB_DOMAIN=t.sb.internal\nAPP_PORT=3000\n'
                'die() { echo "[error] $*" >&2; exit 1; }\n' % (self.dir, self.state))
        return head + body + fakes + tail

    def ls_script(self, fakes=None, tail='cmd_ls\n'):
        """cmd_ls / cmd_status だけを Proxmox 抜きで走らせる（pve_vms / pool_ip を偽装）"""
        text = SCRIPT.read_text()
        body = text[text.index('cmd_ls() {'):text.index('case "${1:-}" in')]
        return (self.script(fakes=fakes or (FAKES + 'SB_PREFIX=sb-t\n'
                            'pve_vms() { echo "9213 sb-t-pj-01 running"; }\n'
                            'pool_ip() { echo 10.77.1.1; }\n'))
                + body + tail)

    def run_take(self, tasks, fakes=FAKES, env=None):
        script = self.script(fakes, tail='cmd_take "$@"\n')
        procs = [subprocess.Popen(['bash', '-c', script, 'sandbox', 'pj', t], text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=dict(os.environ, **(env or {}))) for t in tasks]
        return [(p.wait(), *p.communicate()) for p in procs]

    def state_json(self):
        return json.loads(pathlib.Path(self.state).read_text())

    def assert_no_double_lease(self, results):
        ok = [r for r in results if r[0] == 0]
        bad = [r for r in results if r[0] != 0]
        self.assertEqual(len(ok), 2, results)          # プールは 2 台
        self.assertEqual(len(bad), 1, results)
        self.assertIn('空きなし', bad[0][2])
        state = self.state_json()
        vmids = [v['vmid'] for v in state.values()]
        self.assertEqual(sorted(vmids), [9201, 9202], state)
        self.assertEqual([v for v in state.values() if 'phase' in v], [], state)

    def test_concurrent_take_never_hands_out_same_vmid(self):
        self.assert_no_double_lease(self.run_take(['101', '102', '103']))

    @unittest.skipUnless(shutil.which('flock'), 'flock で取る側の経路')
    def test_concurrent_take_is_serialized_without_flock(self):
        # Mac 既定には flock が無い。mkdir フォールバックでも直列化されること
        results = self.run_take(['101', '102', '103'], env={'SANDBOX_LOCK_MKDIR': '1'})
        self.assert_no_double_lease(results)
        self.assertFalse(os.path.exists(self.state + '.lock.d'))

    def test_reservation_is_removed_when_take_fails(self):
        rc, out, err = self.run_take(['101'], fakes=FAKES + 'rollback() { die "rollback が通らない"; }\n')[0]
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.state_json(), {})
        self.assertIn('仮予約を取り消した', err)
        self.assertFalse(os.path.exists(self.state + '.lock.d'))

    def test_take_refuses_when_task_already_present(self):
        pathlib.Path(self.state).write_text(
            '{"101":{"vmid":9201,"name":"sb-t-pj-01","ip":"10.77.1.1","pj":"pj","since":"x"}}')
        rc, out, err = self.run_take(['101'])[0]
        self.assertNotEqual(rc, 0, out)
        self.assertIn('貸出中', err)
        self.assertEqual(list(self.state_json()), ['101'])

    def test_ls_shows_one_row_per_vm_when_two_tasks_share_it(self):
        """同じ vmid を 2 チケットが持つ台帳でも ls は VM 1 台 = 1 行。TASK 列は「221,222」（237）"""
        pathlib.Path(self.state).write_text(
            '{"221":{"vmid":9213,"name":"sb-t-pj-01","ip":"10.77.1.1","pj":"pj","since":"2026-09-06T10:00:00+09:00"},'
            ' "222":{"vmid":9213,"name":"sb-t-pj-01","ip":"10.77.1.1","pj":"pj","since":"2026-09-06T11:00:00+09:00"}}')
        r = subprocess.run(['bash', '-c', self.ls_script()], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [l.split() for l in r.stdout.splitlines() if l.strip() and not l.startswith('TASK')]
        self.assertEqual(len(rows), 1, r.stdout)                 # 行が割れない（console の parse_ls は列数の合わない行を捨てる）
        self.assertEqual(rows[0][:4], ['221,222', 'sb-t-pj-01', '9213', '10.77.1.1'])

    def take_one(self, task, fakes=FAKES):
        """take を 1 本ずつ走らせる（内訳の文言を見るので、並行ではなく順番に）"""
        return self.run_take([task], fakes=fakes)[0]

    def test_take_error_reports_pool_breakdown(self):
        """空きなしのエラーは内訳（定義 / 実体 / 貸出）と次の一手を言う（241）"""
        self.assertEqual(self.take_one('101')[0], 0)
        self.assertEqual(self.take_one('102')[0], 0)
        rc, out, err = self.take_one('103')
        self.assertNotEqual(rc, 0, out)
        for want in ('空きなし', '定義 3 台', '実体 2 台', '貸出 2 台', '未構築 1 台', '40-pool.sh pj 1'):
            self.assertIn(want, err, err)

    def test_take_error_counts_vms_without_a_clean_snapshot(self):
        """clean が無くて飛ばした台数も内訳に出す（返却待ちか手直しかを分けるため）"""
        no_clean = FAKES.replace('pve_has_clean() { sleep 0.5; return 0; }\n',
                                 'pve_has_clean() { if [[ "$1" == 9202 ]]; then return 1; fi; return 0; }\n')
        self.assertEqual(self.take_one('101', fakes=no_clean)[0], 0)
        rc, out, err = self.take_one('102', fakes=no_clean)
        self.assertNotEqual(rc, 0, out)
        self.assertIn('貸出 1 台', err)
        self.assertIn('clean 無し 1 台', err)

    def test_status_separates_defined_from_actual(self):
        """sandbox status は PJ ごとに 定義 / 実体 / 貸出 / 空き を別の列で出す（241）"""
        pathlib.Path(self.state).write_text(
            '{"101":{"vmid":9201,"name":"sb-t-pj-01","ip":"10.77.1.1","pj":"pj","since":"x"}}')
        fakes = (FAKES.replace('pool_list() { echo "9201 sb-t-pj-01 10.77.1.1 stopped"; echo "9202 sb-t-pj-02 10.77.1.2 stopped"; }\n', '')
                 + 'SB_PREFIX=sb-t\n'
                 'pve_vms() { echo "9201 sb-t-pj-01 running"; echo "9202 sb-t-pj-02 stopped"; echo "9299 sb-t-base stopped"; }\n'
                 'pool_ip() { echo 10.77.1.1; }\n')
        r = subprocess.run(['bash', '-c', self.ls_script(fakes=fakes, tail='cmd_status\n')],
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [l.split() for l in r.stdout.splitlines() if l.strip()]
        self.assertEqual(rows[0], ['PJ', 'DEFINED', 'ACTUAL', 'LENT', 'FREE'])
        self.assertEqual([row for row in rows[1:] if row[0] == 'pj'], [['pj', '3', '2', '1', '1']], r.stdout)

    def test_status_reads_the_defined_size_from_the_environment(self):
        """定義台数の正本は SANDBOX_POOL_PER_PJ（dispatch と同じ変数）"""
        pathlib.Path(self.state).write_text('{}')
        fakes = (FAKES.replace('pool_list() { echo "9201 sb-t-pj-01 10.77.1.1 stopped"; echo "9202 sb-t-pj-02 10.77.1.2 stopped"; }\n', '')
                 + 'SB_PREFIX=sb-t\n'
                 'pve_vms() { echo "9201 sb-t-pj-01 running"; }\n'
                 'pool_ip() { echo 10.77.1.1; }\n')
        r = subprocess.run(['bash', '-c', self.ls_script(fakes=fakes, tail='cmd_status pj\n')], text=True,
                           env=dict(os.environ, SANDBOX_POOL_PER_PJ='5'), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [l.split() for l in r.stdout.splitlines() if l.strip()]
        self.assertEqual(rows[1], ['pj', '5', '1', '0', '1'], r.stdout)

    def test_usage_excerpt_covers_status(self):
        """引数無しのときに出る使い方（末尾の sed の行範囲）に status が入っている。

        設定ファイルが要るので CLI は起動できない。範囲を読み取って同じ行を切り出す
        """
        text = SCRIPT.read_text()
        m = re.search(r"sed -n '(\d+),(\d+)p'", text)
        self.assertIsNotNone(m, '使い方を出す sed が見つからない')
        excerpt = text.splitlines()[int(m.group(1)) - 1:int(m.group(2))]
        self.assertTrue(all(l.startswith('#') for l in excerpt), excerpt[-3:])   # 範囲が使い方コメントの外に出ていない
        self.assertIn('sandbox status', '\n'.join(excerpt))

    def test_release_deletes_under_lock(self):
        pathlib.Path(self.state).write_text(
            '{"101":{"vmid":9201,"name":"sb-t-pj-01","ip":"10.77.1.1","pj":"pj","since":"x"}}')
        r = subprocess.run(['bash', '-c', self.script(tail='cmd_release "$@"\n'), 'sandbox', '101'],
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.state_json(), {})


# rollback() 本体を走らせるための偽装（245）。pve_rollback は $CALLS に呼び出し回数を数え、
# FAIL_N 回目までは Proxmox の lock timeout と同じ体裁で失敗する
ROLLBACK_FAKES = FAKES.replace('rollback() { return 0; }\n', '') + '''
pve_lock() { :; }
pve_status() { echo running; }
pve_start() { return 0; }
pve_rollback() {
  local n; n=$(( $(cat "$CALLS" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$CALLS"
  if (( n <= FAIL_N )); then
    echo "[api] task: stopped can't lock file '/var/lock/qemu-server/lock-$1.conf' - got timeout" >&2
    return 1
  fi
  return 0
}
'''

LENT = ('{"101":{"vmid":9201,"name":"sb-t-pj-01","ip":"10.77.1.1","pj":"pj",'
        '"since":"2026-09-06T10:00:00+09:00"}}')


@unittest.skipUnless(shutil.which('jq'), 'jq required by sandbox CLI')
class SandboxRollbackFailureTest(unittest.TestCase):
    """rollback が失敗した release / reset は非 0 で終わり、state を消さないこと（245）"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state = os.path.join(self.dir, 'state.json')
        pathlib.Path(self.state).write_text(LENT)
        pathlib.Path(self.dir, 'keys.json').write_text(POOL_KEY)
        self.calls = os.path.join(self.dir, 'calls')

    script = SandboxTakeTest.script
    state_json = SandboxTakeTest.state_json

    def run_cmd(self, cmd, *args, fail_n=0):
        script = self.script(fakes=ROLLBACK_FAKES, tail='cmd_%s "$@"\n' % cmd)
        env = dict(os.environ, CALLS=self.calls, FAIL_N=str(fail_n),
                   SB_ROLLBACK_TRIES='3', SB_ROLLBACK_WAIT='0')
        return subprocess.run(['bash', '-c', script, 'sandbox', *args], text=True, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def call_count(self):
        return int(pathlib.Path(self.calls).read_text().strip())

    def test_release_retries_transient_rollback_failure(self):
        r = self.run_cmd('release', '101', fail_n=1)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.state_json(), {})
        self.assertIn('[warn]', r.stderr)
        self.assertIn('rollback 失敗 (1/3)', r.stderr)
        self.assertIn('[ok] released', r.stdout)
        self.assertEqual(self.call_count(), 2)

    def test_release_keeps_state_when_rollback_keeps_failing(self):
        r = self.run_cmd('release', '101', fail_n=99)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertEqual(list(self.state_json()), ['101'])
        self.assertIn('巻き戻しに失敗', r.stderr)
        self.assertIn('state は保持', r.stderr)
        self.assertIn('sandbox release 101', r.stderr)
        self.assertNotIn('[ok] released', r.stdout)
        self.assertEqual(self.call_count(), 3)

    def test_release_force_deletes_state_even_if_rollback_fails(self):
        r = self.run_cmd('release', '101', '--force', fail_n=99)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.state_json(), {})
        self.assertIn('--force', r.stderr)

    def test_reset_fails_nonzero_when_rollback_keeps_failing(self):
        r = self.run_cmd('reset', '101', fail_n=99)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertEqual(list(self.state_json()), ['101'])
        self.assertIn('巻き戻しに失敗', r.stderr)
        self.assertIn('貸出は継続', r.stderr)
