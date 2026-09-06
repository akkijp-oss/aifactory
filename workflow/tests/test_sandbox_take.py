"""Regression: 同時に走る sandbox take が同じ VM を二重に貸し出さないこと（234）。

Proxmox を呼ぶ関数は偽装し、sandbox/bin/sandbox から state / take の区画だけを切り出して bash で走らせる。
"""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / 'sandbox/bin/sandbox'

# Proxmox / ssh / DNS を叩く関数を空にし、プールは 2 台に固定する。
# pve_has_clean の sleep で「選定 → 予約」の窓を広げ、ロックが無ければ必ず衝突するようにする
FAKES = '''
load_pj() { CUR_PJ=$1; CLAUDE_CODE_OAUTH_TOKEN=dummy; }
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

    def script(self, fakes=FAKES, tail=''):
        text = SCRIPT.read_text()
        body = text[text.index('# ---------- state（台帳とロック）'):text.index('mask() {')]
        head = ('set -euo pipefail\n'
                'CONF_DIR=%s\nSTATE=%s\nSB_DOMAIN=t.sb.internal\nAPP_PORT=3000\n'
                'die() { echo "[error] $*" >&2; exit 1; }\n' % (self.dir, self.state))
        return head + body + fakes + tail

    def ls_script(self):
        """cmd_ls だけを Proxmox 抜きで走らせる（pve_vms / pool_ip を偽装）"""
        text = SCRIPT.read_text()
        body = text[text.index('cmd_ls() {'):text.index('case "${1:-}" in')]
        return (self.script(fakes=FAKES + 'SB_PREFIX=sb-t\n'
                            'pve_vms() { echo "9213 sb-t-pj-01 running"; }\n'
                            'pool_ip() { echo 10.77.1.1; }\n')
                + body + 'cmd_ls\n')

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

    def test_release_deletes_under_lock(self):
        pathlib.Path(self.state).write_text(
            '{"101":{"vmid":9201,"name":"sb-t-pj-01","ip":"10.77.1.1","pj":"pj","since":"x"}}')
        r = subprocess.run(['bash', '-c', self.script(tail='cmd_release "$@"\n'), 'sandbox', '101'],
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.state_json(), {})
