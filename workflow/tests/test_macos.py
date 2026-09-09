import base64
import datetime
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import types
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('macos_backend', ROOT/'workflow/lib/macos.py')
macos=importlib.util.module_from_spec(spec);spec.loader.exec_module(macos)
REAL_CLIENT=macos.Client

# 「他 run が worker を使っている」ときの待ち（チケット 373）は take_waiting / fail_before_start の本物を通す。
# runner を module として読み、置き場だけ一時 dir に向ける（test_macos_preserve.py と同じ流儀）
rspec=importlib.util.spec_from_loader('macos_lease_run', importlib.machinery.SourceFileLoader('macos_lease_run', str(ROOT/'workflow/bin/run')))
run_mod=importlib.util.module_from_spec(rspec);rspec.loader.exec_module(run_mod)
run_mod.paths=types.SimpleNamespace(project_dir=run_mod.paths.project_dir, PROJECT_DIRS=run_mod.paths.PROJECT_DIRS, RUNS=None, run_path=None)
MacWaitRun=macos.backend(run_mod.Run)
KB=ROOT/'kanban/bin/kb'


class MacBackendTest(unittest.TestCase):
    def make_run(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        run=object.__new__(macos.backend(object))
        run.dry=False;run.keep=False;run.work='/Users/admin/work/209'
        run.env_file=run.work+'/runtime.env'
        run.run_dir=pathlib.Path(tmp.name);run.project={'worker':'mac1'}
        run.state={};run.save=lambda:None;run.log=lambda _:None
        run.client=types.SimpleNamespace(lease='lease-1')
        return run

    def test_computer_configuration_is_opt_in_and_uses_guest_path(self):
        run=self.make_run()
        run.scp_to=lambda *args:self.fail('disabled project transferred MCP config')
        run.configure_computer()
        run.project.update(computer_use=True,app_dir='/Users/admin/app')
        run.state['backend']='macos-pull'
        sent=[];run.scp_to=lambda *args:sent.append(args)
        run.configure_computer()
        config=json.loads(sent[0][0].read_text())['mcpServers']['computer']
        self.assertEqual(config['command'],'/Users/admin/.local/lib/aifactory-computer/aifactory-computer')
        self.assertEqual(config['args'],['-mode','mcp','-artifacts',run.work])
        self.assertEqual(sent[0][1],run.work+'/computer-mcp.json')
        run.state['backend']='windows-pull'
        run.configure_computer()
        config=json.loads(sent[-1][0].read_text())['mcpServers']['computer']
        self.assertEqual(config['command'],r'C:\ProgramData\AIFactoryWorker\bin\aifactory-computer.exe')

    def test_computer_use_rejects_non_desktop_backends(self):
        import jsonschema
        schema=json.loads((ROOT/'workflow/kit/schema/project.schema.json').read_text())
        project={'name':'test','repo':'owner/repo','base_branch':'main','app_dir':'/Users/admin/app','gates':'gates.sh','computer_use':True}
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(project,schema)
        for backend in ('macos-pull','windows-pull'):
            jsonschema.validate({**project,'backend':backend,'worker':'test-worker','gates':'gates.ps1' if backend=='windows-pull' else 'gates.sh','app_dir':'C:/work/app' if backend=='windows-pull' else '/Users/admin/app'},schema)

    def test_bad_artifacts_never_release_guest(self):
        for name,checksum in [('../escape',hashlib.sha256(b'ok').hexdigest()),('report.md','bad')]:
            run=self.make_run()
            run.sb=lambda _:json.dumps({'files':{name:{'data':base64.b64encode(b'ok').decode(),'sha256':checksum}}})
            run.client.execute=lambda *a,**k:self.fail('released without verifying artifacts')
            with self.assertRaises(RuntimeError):run.release()
            self.assertNotIn('artifacts_received',run.state)

    def test_artifacts_received_before_guest_release(self):
        run=self.make_run();content=b'# Report\n'
        run.sb=lambda _:json.dumps({'files':{'report.md':{'data':base64.b64encode(content).decode(),'sha256':hashlib.sha256(content).hexdigest()}},'skipped':[]})
        def execute(kind):
            self.assertEqual(kind,'guest-release')
            self.assertTrue(run.state['artifacts_received'])
            self.assertEqual((run.run_dir/'work/report.md').read_bytes(),content)
            return 'release-op',types.SimpleNamespace(returncode=0)
        run.client.execute=execute
        calls=[];run.client.store=types.SimpleNamespace(release_lease=lambda *args:calls.append(args))
        run.release()
        self.assertTrue(run.state['released'])
        self.assertEqual(calls,[('mac1','lease-1','release-op')])

    def collect(self, tmp):
        """作業ディレクトリを実際に列挙させて、ゲスト側スクリプトの生の出力を返す。"""
        return subprocess.run([sys.executable, '-c', macos.COLLECT_SCRIPT, str(tmp)],
                              text=True, capture_output=True)

    def work_tree(self):
        """回収対象外（ディレクトリ・symlink・4 MiB 超）を全部含む作業ディレクトリを作る。"""
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        root=pathlib.Path(tmp.name)
        (root/'report.md').write_bytes(b'# Report\n')
        (root/'runtime.env').write_bytes(b'export GH_TOKEN=secret\n')
        (root/'shots').mkdir();(root/'shots'/'screen.png').write_bytes(b'png')
        (root/'link').symlink_to(root/'report.md')
        (root/'big.bin').write_bytes(b'x'*(4*1024*1024+1))
        # 上限超過の「後ろ」に来る小さいファイル。飛ばした分を total に数えていれば回収される
        (root/'zz-review.md').write_bytes(b'# Review\n')
        return root

    def test_non_regular_and_oversized_entries_are_skipped_not_fatal(self):
        # チケット 277: shots/ のようなサブディレクトリ 1 つで run 全体が止まり lease が残っていた
        root=self.work_tree()
        result=self.collect(root)
        self.assertEqual(result.returncode,0,result.stderr[-500:])
        manifest=json.loads(result.stdout)
        self.assertEqual(sorted(manifest['files']),['report.md','zz-review.md'])
        skipped={item['name']:item['reason'] for item in manifest['skipped']}
        for name,reason in (('shots','directory'),('link','symlink'),('big.bin','size')):
            with self.subTest(name=name):
                self.assertEqual(skipped.get(name),reason)
        self.assertNotIn('runtime.env',manifest['files'])
        self.assertNotIn('runtime.env',skipped)

    def test_skipped_artifacts_are_recorded_and_guest_released(self):
        run=self.make_run();content=b'# Report\n'
        run.sb=lambda _:json.dumps({'files':{'report.md':{'data':base64.b64encode(content).decode(),
                                                          'sha256':hashlib.sha256(content).hexdigest()}},
                                    'skipped':[{'name':'shots','reason':'directory'}]})
        released=[]
        run.client.execute=lambda kind:(released.append(kind),('release-op',types.SimpleNamespace(returncode=0)))[1]
        run.client.store=types.SimpleNamespace(release_lease=lambda *args:None)
        run.release()
        self.assertEqual(released,['guest-release'])
        self.assertTrue(run.state['released'])
        self.assertEqual(run.state['artifacts_skipped'],[{'name':'shots','reason':'directory'}])

    def test_guest_reported_skips_are_quarantined_before_state(self):
        run=self.make_run()
        run.sb=lambda _:json.dumps({'files':{},'skipped':
            [{'name':'a'*400,'reason':'; rm -rf /'},{'name':'shots','reason':'symlink'},'not-a-dict']
            +[{'name':f'n{i}','reason':'size'} for i in range(200)]})
        run.client.execute=lambda kind:('release-op',types.SimpleNamespace(returncode=0))
        run.client.store=types.SimpleNamespace(release_lease=lambda *args:None)
        run.release()
        recorded=run.state['artifacts_skipped']
        self.assertEqual(len(recorded),128)
        self.assertEqual(len(recorded[0]['name']),255)
        self.assertEqual(recorded[0]['reason'],'other')
        self.assertEqual(recorded[1],{'name':'shots','reason':'symlink'})
        self.assertTrue(all(set(item)=={'name','reason'} for item in recorded))

    def test_manifest_without_files_key_is_refused(self):
        for manifest in ({},{'report.md':{'data':'','sha256':''}},{'files':[]}):
            run=self.make_run()
            run.sb=lambda _,manifest=manifest:json.dumps(manifest)
            run.client.execute=lambda *a,**k:self.fail('released on an unreadable manifest')
            with self.assertRaises(RuntimeError):run.release()
            self.assertNotIn('artifacts_received',run.state)

    def test_nonzero_gate_without_fail_line_is_failure(self):
        run=self.make_run();run.project_dir=pathlib.Path('/project');run.project['gates']='gates.sh'
        run.base='main';run.state['history']=[];run.set_current=lambda *args:None
        run.scp_to=lambda *args:None;run.run_remote=lambda *args:(7,'tool crashed\n')
        run.sb=lambda *args,**kwargs:''
        self.assertEqual(run.run_code({'id':'gates','code':'gates.sh'}),(False,'tool crashed\n'))

    def test_keep_retains_guest_after_receiving_artifacts(self):
        run=self.make_run();run.keep=True;run.sb=lambda _: '{"files":{}}'
        run.client.execute=lambda *args,**kwargs:self.fail('keep released guest')
        run.release()
        self.assertTrue(run.state['artifacts_received'])
        self.assertNotIn('released',run.state)

    def test_resume_retries_provisioning_only_in_clean_owned_guest(self):
        run=self.make_run()
        run.client.store=types.SimpleNamespace(workers=lambda:[{'id':'mac1','lease':{'id':'lease-1'}}])
        replies=iter(['','provisionable']);run.sb=lambda *a,**k:next(replies)
        calls=[];run.setup_project=lambda:calls.append('provision')
        run.resume_guest('lease-1')
        self.assertEqual(calls,['provision'])

    def test_resume_refuses_unknown_lease_or_incomplete_repository(self):
        for lease,replies in [('other',[]),('lease-1',['',''])]:
            run=self.make_run()
            run.client.store=types.SimpleNamespace(workers=lambda:[{'id':'mac1','lease':{'id':lease}}])
            answers=iter(replies);run.sb=lambda *a,**k:next(answers)
            run.setup_project=lambda:self.fail('recreated an unverified guest')
            with self.assertRaises(RuntimeError):run.resume_guest('lease-1')

    def test_builtin_sync_base_step_is_accepted_but_unknown_scripts_are_not(self):
        # sync-base は runner 内蔵（kit/steps/ にファイルが無い）ので、pull worker でも拒否しない（チケット 239）
        class Base:
            def __init__(self,wf):
                self.wf=wf;self.project={'app_dir':'/Users/admin/app','worker':'mac1'}
                self.task='209';self.resume=False;self.state={}
        MacRun=macos.backend(Base)
        MacRun({'steps':[{'code':'gates.sh'},{'code':'sync-base'},{'code':'pr-create.sh'}]})
        with self.assertRaises(ValueError):MacRun({'steps':[{'code':'pr-merge.sh'}]})

    def test_automerge_step_is_skipped_when_project_has_no_auto_merge(self):
        # ADR-0042 で全 workflow に automerge step が入った。auto_merge の無い PJ では runner が工程ごと飛ばすので、
        # pull worker の未対応判定でも拒否しない（asura #381 が起動前に落ちた）。auto_merge がある PJ は従来どおり拒否する
        class Base:
            def __init__(self,wf,auto_merge=None):
                self.wf=wf;self.project={'app_dir':'/Users/admin/app','worker':'mac1'}
                self.task='381';self.resume=False;self.state={};self.auto_merge=auto_merge
        MacRun=macos.backend(Base)
        steps=[{'code':'gates.sh'},{'code':'sync-base'},{'code':'pr-create.sh'},{'code':'pr-automerge.sh'}]
        MacRun(steps and {'steps':steps})
        with self.assertRaises(ValueError):MacRun({'steps':steps},auto_merge={'method':'merge'})


class MacLeaseWaitTest(unittest.TestCase):
    """worker を他 run が使っている間の `kb run --wait`（チケット 373）。

    Mac 実機も claude も VM も使わない。偽の worker queue（`workers()` の戻りで lease を出し入れする）だけ差し替え、
    take_waiting / fail_before_start / kb apply_result は本物を通す。
    - 他 run の lease は「待てば解ける失敗」（Proxmox のプール満杯と同じ）で、`--wait` の間は wait-vm で待つ
    - 上限を超えても failed / blocked にせず todo に戻し、note に「誰がいつから使っているか」を残す
    - 「lease は残す」は自 run の lease が実在するときだけ
    """

    LEASE_CREATED = 1757415600      # 他 run が lease を取った時刻（epoch）

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        run_mod.paths.RUNS = self.ws / 'runs'
        self.ticket = self.ws / 'ticket.md'
        self.ticket.write_text('# 調査: lease 待ちの再現\n\n偽の worker で lease を握らせる。\n', encoding='utf-8')
        # lease を持っている他 PJ の run（この名前が note に出ること）
        self.holder = self.ws / 'runs' / '2026-09-09-termarium-251'
        self.holder.mkdir(parents=True)
        (self.holder / 'state.json').write_text(json.dumps({'lease': 'run-251-abc'}), encoding='utf-8')
        os.environ['AIFACTORY_WAIT_POLL_S'] = '0.01'
        self.addCleanup(os.environ.pop, 'AIFACTORY_WAIT_POLL_S', None)
        self.addCleanup(setattr, macos, 'Client', REAL_CLIENT)
        self.acquired = []      # store.acquire の呼ばれ方
        self.logs = []          # runner の log 行
        self.seen = []          # workers() が呼ばれた時点の state.json の写し

    def since(self):
        return datetime.datetime.fromtimestamp(self.LEASE_CREATED).astimezone().isoformat(timespec='seconds')

    def build(self, task, wait_s=0, busy_calls=0):
        """偽の Mac worker で run を組む。busy_calls 回目までは他 run の lease が居る"""
        r = object.__new__(MacWaitRun)
        run_mod.Run.__init__(r, 'kumitate', str(task), 'research', str(self.ticket), wait_s=wait_s)
        r.project = {**r.project, 'worker': 'mac1'}
        r.work = '/Users/admin/work/' + str(task)
        r.env_file = r.work + '/runtime.env'
        r.client = None
        r.run_lock = None
        r.state['backend'] = 'macos-pull'
        r.state['worker'] = 'mac1'
        calls = {'n': 0}

        def workers():
            calls['n'] += 1
            self.seen.append(json.loads((r.run_dir / 'state.json').read_text(encoding='utf-8')))
            busy = calls['n'] <= busy_calls
            return [{'id': 'mac1', 'online': True, 'info': {'lifecycle': True},
                     'lease': {'id': 'run-251-abc', 'created': self.LEASE_CREATED} if busy else None,
                     'operation': None}]

        store = types.SimpleNamespace(workers=workers, acquire=lambda w, l: self.acquired.append((w, l)))
        client = types.SimpleNamespace(store=store, lease=None,
                                       execute=lambda *a, **k: ('op-1', types.SimpleNamespace(returncode=0, stdout='')))
        macos.Client = lambda *a, **k: client
        r.setup_project = lambda: None
        r.log = lambda m: self.logs.append(m)
        r.t0 = time.time()
        return r

    def test_a_lease_held_by_another_run_is_waited_out(self):
        """他 run が lease を持っている間は wait-vm で待ち、空いたら take する（完了条件 1）"""
        r = self.build(373, wait_s=60, busy_calls=2)
        r.take_waiting()
        self.assertEqual(len(self.seen), 3)                                  # 使用中 2 回 → 3 回目で取れた
        self.assertEqual(self.acquired, [('mac1', r.state['lease'])])
        self.assertTrue(r.state['lease'].startswith('run-373-'), r.state['lease'])
        self.assertEqual(self.seen[0]['current']['step'], 'take')            # 待つ前（238 の初期書き込み）
        waits = [s['current'] for s in self.seen[1:]]
        self.assertEqual([w['step'] for w in waits], ['wait-vm', 'wait-vm'], waits)
        self.assertEqual(waits[0]['since'], waits[1]['since'])               # 経過時間を振り出しに戻さない
        self.assertTrue([m for m in self.logs if '2026-09-09-termarium-251 が使用中' in m], self.logs)
        # 二重 flock で落ちない（同じ run が take を呼び直す）
        self.assertIsNotNone(r.run_lock)

    def test_waiting_past_the_limit_stays_todo_with_the_holder_in_the_note(self):
        """上限まで待って空かなければ、lease は取らず wait_timeout で終わり、理由に使用中の run と開始時刻が残る（完了条件 2）"""
        r = self.build(374, wait_s=1, busy_calls=10 ** 9)
        self.assertEqual(r.main(), 2)
        s = json.loads((r.run_dir / 'state.json').read_text(encoding='utf-8'))
        self.assertEqual((s['result'], s['next'], s['failure']), ('failed', 'human', 'wait_timeout'))
        self.assertGreaterEqual(s['waited_s'], 1)
        self.assertNotIn('lease', s)
        self.assertIn('mac1 は 2026-09-09-termarium-251 が使用中', s['wait_reason'])
        self.assertIn(self.since(), s['wait_reason'])
        self.assertEqual(self.acquired, [])
        self.assertFalse([m for m in self.logs if 'lease は残す' in m], self.logs)

    def test_the_kb_note_names_the_holder_and_keeps_the_ticket_todo(self):
        """kb は wait_reason を note に写し、チケットは todo のまま（blocked にしない。完了条件 2）"""
        env = dict(os.environ, AIFACTORY_WORKSPACE=str(self.ws))
        new = subprocess.run([sys.executable, str(KB), 'new', 'kumitate', 'research', 'lease 待ちの再現',
                              '--body', '-', '--id', '375'], input='# lease 待ち\n', text=True, capture_output=True, env=env)
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)
        name = '2026-09-09-kumitate-375'
        (self.ws / 'runs' / name).mkdir(parents=True)
        state = {'result': 'failed', 'next': 'human', 'finished': '2026-09-09T12:09:00+09:00', 'waited_s': 1800,
                 'failure': 'wait_timeout',
                 'wait_reason': f'mac1 は 2026-09-09-termarium-251 が使用中（{self.since()}）'}
        (self.ws / 'runs' / name / 'state.json').write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        sync = subprocess.run([sys.executable, str(KB), 'sync', '375', '--run', name], text=True, capture_output=True, env=env)
        self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
        show = subprocess.run([sys.executable, str(KB), 'show', '375'], text=True, capture_output=True, env=env)
        head = show.stdout.split('-' * 60)[0].splitlines()
        t = {l.split(' ', 1)[0]: l.split(' ', 1)[1].strip() for l in head if l.strip()}
        self.assertEqual(t['status'], 'todo', t)
        self.assertIn('2026-09-09-termarium-251 が使用中', t['note'])
        self.assertIn('dispatch', t['note'])

    def test_the_retained_lease_note_is_only_for_this_runs_lease(self):
        """「調べられるよう lease は残す」は自 run の lease が実在するときだけ（完了条件 3）"""
        r = self.build(376)
        r.fail_before_start(RuntimeError('guest prepare failed'))
        self.assertFalse([m for m in self.logs if 'lease は残す' in m], self.logs)
        self.logs.clear()
        r2 = self.build(377)
        r2.state['lease'] = 'run-377-abcdef'
        r2.fail_before_start(RuntimeError('guest prepare failed'))
        self.assertTrue([m for m in self.logs if 'lease は残す' in m], self.logs)

    def test_without_wait_a_busy_worker_goes_back_to_todo_not_blocked(self):
        """`--wait` 無しでも、他 run の lease は「直す所が無い失敗」。blocked にせず todo に戻す（チケット 373 (b)）"""
        r = self.build(378, wait_s=0, busy_calls=10 ** 9)
        self.assertEqual(r.main(), 2)
        s = json.loads((r.run_dir / 'state.json').read_text(encoding='utf-8'))
        self.assertEqual((s['result'], s['failure']), ('failed', 'wait_timeout'))
        self.assertNotIn('waited_s', s)                                      # 待っていないので「0 秒待った」とは書かない
        self.assertIn('2026-09-09-termarium-251 が使用中', s['wait_reason'])
        self.assertEqual(self.acquired, [])
        self.assertFalse([m for m in self.logs if 'lease は残す' in m], self.logs)


if __name__=='__main__':unittest.main()
