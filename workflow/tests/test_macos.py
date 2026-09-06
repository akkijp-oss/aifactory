import base64
import datetime
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import shlex
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

    def test_display_needs_the_mac_backend_and_a_complete_in_range_size(self):
        import jsonschema
        schema=json.loads((ROOT/'workflow/kit/schema/project.schema.json').read_text())
        project={'name':'test','repo':'owner/repo','base_branch':'main','app_dir':'/Users/admin/app','gates':'gates.sh','worker':'test-worker'}
        jsonschema.validate({**project,'backend':'macos-pull','display':{'width':1600,'height':1000}},schema)
        jsonschema.validate({**project,'backend':'macos-pull'},schema)
        # width/height が揃っていて schema の値域に収まるものだけ。scale はこの PR では受け付けない（ADR-0057）
        for bad in ({'width':1600},{'height':1000},{'width':1600,'height':1000,'scale':2},
                    {'width':640,'height':1000},{'width':2600,'height':1000},
                    {'width':1600,'height':400},{'width':1600,'height':1000.5},{}):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate({**project,'backend':'macos-pull','display':bad},schema)
        for other in ({},{'backend':'windows-pull','app_dir':'C:/work/app','gates':'gates.ps1'},{'backend':'linux-pull'}):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate({**project,**other,'display':{'width':1600,'height':1000}},schema)

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

    def test_automerge_step_is_accepted_with_or_without_auto_merge(self):
        # ADR-0042 で全 workflow に automerge step が入った。auto_merge の無い PJ では runner が工程ごと飛ばし、
        # auto_merge のある PJ では guest の中で kit の script を走らせる（チケット 386）。どちらも起動前に拒否しない
        # （asura #381 は auto_merge を書いた途端に起動前 ValueError で落ちていた）
        class Base:
            def __init__(self,wf,auto_merge=None):
                self.wf=wf;self.project={'app_dir':'/Users/admin/app','worker':'mac1'}
                self.task='381';self.resume=False;self.state={};self.auto_merge=auto_merge
        MacRun=macos.backend(Base)
        steps=[{'code':'gates.sh'},{'code':'sync-base'},{'code':'pr-create.sh'},{'code':'pr-automerge.sh'}]
        MacRun({'steps':steps})
        MacRun({'steps':steps},auto_merge={'method':'merge'})
        # 表で unsupported と宣言した step と、表に無い step（足した人が対応表を更新していない）は今までどおり拒否する
        with self.assertRaises(ValueError):MacRun({'steps':[{'code':'pr-merge.sh'}]})
        with self.assertRaises(ValueError):MacRun({'steps':[{'code':'brand-new.sh'}]})


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
        self.executed = []      # client.execute に渡った (kind, payload)

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
        def execute(kind, payload=None, *a, **k):
            self.executed.append((kind, payload))
            return ('op-1', types.SimpleNamespace(returncode=0, stdout=''))

        client = types.SimpleNamespace(store=store, lease=None, execute=execute)
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

    def test_a_pj_display_reaches_guest_prepare_and_absence_changes_nothing(self):
        """project.yml の display だけが guest-prepare の payload に乗る（343）"""
        r = self.build(343)
        r.project = {**r.project, 'display': {'width': 1600, 'height': 1000}}
        r.take()
        self.assertEqual(self.executed, [('guest-prepare', {'width': 1600, 'height': 1000})])
        self.executed.clear()
        plain = self.build(344)
        plain.take()
        self.assertEqual(self.executed, [('guest-prepare', {})])

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


# 偽 sandbox。鍵の選び方の正本は bash の keys_pick / _pool_apply_locked（test_sandbox_keys.py が固定する）ので、
# ここが写すのは「どう呼ばれるか」と「stdout の形」だけ。--current が有効な鍵ならそれを、駄目なら先頭を返す
FAKE_SANDBOX = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
argv = sys.argv[1:]
calls = pathlib.Path(os.environ['FAKE_SANDBOX_CALLS'])
calls.write_text(calls.read_text(encoding='utf-8') + json.dumps(argv, ensure_ascii=False) + '\n', encoding='utf-8')
if argv[:2] == ['gh-app', 'token']:
    print('ghs_fake-token-gh'); sys.exit(0)
if argv[:2] == ['keys', 'used']:
    sys.exit(0)
if argv[:2] != ['keys', 'pick']:
    sys.exit('[error] unexpected: ' + ' '.join(argv))
opts, rest, i = {}, argv[2:], 0
while i < len(rest):
    a = rest[i]
    if '=' in a: k, v = a.split('=', 1); opts[k] = v; i += 1
    elif a == '--json': opts[a] = True; i += 1
    else: opts[a] = rest[i + 1]; i += 2
enabled = json.loads(pathlib.Path(os.environ['FAKE_POOL']).read_text(encoding='utf-8'))
if not enabled:
    sys.exit('[error] 鍵なし: Fable に使う、Opus・Sonnet・Haiku に使う鍵が鍵プールに無い')
cur = dict(kv.split('=', 1) for kv in opts.get('--current', '').split(',') if '=' in kv)
keys, env = {}, {}
for group, families in (('fable', ['FABLE']), ('other', ['OPUS', 'SONNET', 'HAIKU'])):
    if group not in opts.get('--need', 'fable,other').split(','): continue
    name = cur[group] if cur.get(group) in enabled else enabled[0]
    keys[group] = name
    for f in families:
        env['CLAUDE_CODE_OAUTH_TOKEN_' + f] = 'fake-token-' + name
        env['CLAUDE_KEY_NAME_' + f] = name
    if group == 'other': env['CLAUDE_CODE_OAUTH_TOKEN'] = 'fake-token-' + name
print(json.dumps({'keys': keys, 'env': env}))
'''


class PullBackendKeyTest(unittest.TestCase):
    """pull backend の Claude の鍵は制御系の鍵プールが正本（チケット 391 / ADR-0044・ADR-0046）。

    実測（2026-09-10）: 長生きした MCP サーバーの環境に残った古い鍵が runner まで素通りし、
    credentials() が env ファイルの source に落ちてその鍵を guest の runtime.env に書いていた。
    無効化済みの鍵が全工程・全モデルで使われ続けたので、
    「環境の鍵は guest に届かない」「系統ごとにプールから選ぶ」「鍵が無ければ書かずに止まる」を固定する。
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)
        fake = self.tmp / 'sandbox' / 'bin' / 'sandbox'
        fake.parent.mkdir(parents=True)
        fake.write_text(FAKE_SANDBOX, encoding='utf-8'); fake.chmod(0o755)
        self.calls = self.tmp / 'calls.jsonl'; self.calls.write_text('', encoding='utf-8')
        self.pool = self.tmp / 'pool.json'; self.set_pool('pool-a', 'pool-b')
        old = macos.ROOT; macos.ROOT = self.tmp
        self.addCleanup(setattr, macos, 'ROOT', old)
        # runner の環境に残った古い鍵と、env ファイルの置き場（プール運用では鍵は入っていない）
        for k, v in (('FAKE_SANDBOX_CALLS', str(self.calls)), ('FAKE_POOL', str(self.pool)),
                     ('XDG_CONFIG_HOME', str(self.tmp / 'config')),
                     ('CLAUDE_CODE_OAUTH_TOKEN', 'fake-token-leaked-n5')):
            self.addCleanup(os.environ.pop, k, None)
            os.environ[k] = v

    def set_pool(self, *names):
        self.pool.write_text(json.dumps(list(names)), encoding='utf-8')

    def picks(self):
        return [json.loads(l) for l in self.calls.read_text(encoding='utf-8').splitlines() if '"pick"' in l]

    def make_run(self):
        run = object.__new__(macos.backend(object))
        run.dry = False; run.keep = False; run.pj = 'kumitate'; run.task = '391'
        run.work = str(self.tmp / 'guest'); pathlib.Path(run.work).mkdir(exist_ok=True)
        run.env_file = run.work + '/runtime.env'
        run.project = {'worker': 'mac1', 'app_dir': str(self.tmp / 'guest' / 'app')}
        run.state = {'history': []}; run.save = lambda: None
        self.logs = []; run.log = self.logs.append
        run.needed_keys = lambda: ['fable', 'other']
        run.sb = self.guest_sb(run)
        return run

    def guest_sb(self, run):
        """偽 guest。runtime.env を実ファイルとして扱い、命令は本物の command() を通して bash に渡す
        （Mac 実機は使わない。runtime.env を source した結果が本番と同じ形になることを見る）"""
        def sb(cmd, input_text=None, check=True):
            r = subprocess.run(['bash', '-c', run.command(cmd)], input=input_text, text=True, capture_output=True,
                               env={k: v for k, v in os.environ.items()
                                    if not k.startswith(('CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_KEY_NAME'))})
            if check and r.returncode: raise RuntimeError(f'guest command failed ({r.returncode})')
            return r.stdout
        return sb

    def env_body(self):
        return pathlib.Path(self.tmp / 'guest' / 'runtime.env').read_text(encoding='utf-8')

    def guest_env(self):
        """guest が source して得る値（quote の仕方には依らない形で見る）"""
        out = {}
        for line in self.env_body().splitlines():
            k, _, v = line[len('export '):].partition('=')
            out[k] = next(iter(shlex.split(v)), '')
        return out

    # ---------- 完了条件 2: 環境の鍵は guest に届かない
    def test_a_key_left_in_the_runner_environment_never_reaches_the_guest(self):
        run = self.make_run()
        run.refresh_token()
        self.assertNotIn('leaked', self.env_body())
        env = self.guest_env()
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN_FABLE'], 'fake-token-pool-a')
        self.assertEqual(env['CLAUDE_KEY_NAME_FABLE'], 'pool-a')
        for f in ('OPUS', 'SONNET', 'HAIKU'):
            self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN_' + f], 'fake-token-pool-a')
            self.assertEqual(env['CLAUDE_KEY_NAME_' + f], 'pool-a')
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN'], 'fake-token-pool-a')   # 無印も系統の鍵で埋める
        self.assertEqual(env['GH_TOKEN'], 'ghs_fake-token-gh')
        self.assertEqual(run.state['keys'], {'fable': 'pool-a', 'other': 'pool-a'})
        need = [a for a in self.picks()[0] if a.startswith('--need=')]
        self.assertEqual(need, ['--need=fable,other'])

    def test_the_pick_is_asked_for_this_task_and_keeps_the_previous_key(self):
        """次の工程は state に残った名前を --current で渡すので、同じ鍵を使い続ける（ADR-0046）"""
        run = self.make_run()
        run.refresh_token(); run.refresh_token()
        first, second = self.picks()
        self.assertIn('--task', first); self.assertEqual(first[first.index('--task') + 1], '391')
        self.assertIn('--pj', first); self.assertEqual(first[first.index('--pj') + 1], 'kumitate')
        self.assertNotIn('--current=fable=pool-a,other=pool-a', first)
        self.assertIn('--current=fable=pool-a,other=pool-a', second)
        self.assertEqual(self.guest_env()['CLAUDE_KEY_NAME_FABLE'], 'pool-a')

    # ---------- 完了条件 3: 無効化すると次の工程から別の鍵
    def test_disabling_the_key_moves_the_next_step_to_another_one(self):
        run = self.make_run()
        run.refresh_token()
        self.assertEqual(run.state['keys']['other'], 'pool-a')
        self.set_pool('pool-b')                                   # pool-a を無効化した
        run.refresh_token()
        self.assertEqual(run.state['keys'], {'fable': 'pool-b', 'other': 'pool-b'})
        self.assertEqual(self.guest_env()['CLAUDE_KEY_NAME_OPUS'], 'pool-b')
        self.assertNotIn('pool-a', self.env_body())

    # ---------- 完了条件 4: 鍵が無ければ書かずに止まる
    def test_an_empty_pool_stops_the_step_without_writing_the_guest_env(self):
        run = self.make_run()
        run.NoKey = run_mod.NoKey
        self.set_pool()
        with self.assertRaises(run_mod.NoKey) as e:
            run.refresh_token()
        self.assertIn('鍵なし:', str(e.exception))
        self.assertFalse((self.tmp / 'guest' / 'runtime.env').exists())
        self.assertNotIn('keys', run.state)

    def test_a_step_that_cannot_get_a_key_pauses_instead_of_failing(self):
        """鍵が取れない工程は「その step が悪い」ではないので、wip を保全して一時停止（PAUSE_KINDS）に載せる"""
        run = self.make_run()
        run.NoKey = run_mod.NoKey; run.WIP_KEY_MESSAGE = run_mod.WIP_KEY_MESSAGE
        run.configure_computer = lambda: self.fail('rented a guest without a key')
        committed = []; run.commit_tracked = committed.append
        self.set_pool()
        ok, info = run.run_agent({'id': 'implement', 'role': 'implementer'})
        self.assertFalse(ok)
        self.assertEqual(run.last_fail['failure'], 'key')
        self.assertEqual(committed, [run_mod.WIP_KEY_MESSAGE])
        self.assertIn('鍵なし:', info)

    # ---------- 秘密の扱い
    def test_the_key_value_never_reaches_the_log_state_or_an_exception(self):
        run = self.make_run()
        run.refresh_token()
        blob = json.dumps(run.state, ensure_ascii=False) + '\n'.join(self.logs)
        self.assertNotIn('fake-token-', blob)
        self.assertIn('pool-a', json.dumps(run.state))          # 名前だけは残す

    # ---------- 完了条件 5: LAUNCHES に pull backend の起動が数えられる
    def test_the_guest_reports_the_pool_key_name_for_launches(self):
        """runtime.env を source した guest で key_probe_command が `(pool: <名前>)` を返し、
        runner の report_key_launch が `sandbox keys used <名前>` を呼ぶところまで（#75）"""
        run = self.make_run()
        run.refresh_token()
        line = run.sb(run_mod.Run.key_probe_command('OPUS'), check=False).strip()
        self.assertEqual(line, 'CLAUDE_CODE_OAUTH_TOKEN_OPUS (pool: pool-a)')
        self.assertEqual(run_mod.Run.POOL_KEY_RE.search(line).group(1), 'pool-a')

    # ---------- 再開判定（ADR-0046）
    def test_take_records_the_needed_purposes_before_preparing(self):
        run = self.make_run()
        run.resume = False; run.run_lock = object(); run.client = None
        with self.assertRaises(Exception):
            run.take()
        self.assertEqual(run.state.get('needed_keys'), ['fable', 'other'])



class PullBackendAutomergeTest(unittest.TestCase):
    """auto_merge のある PJ の automerge 工程（チケット 386 / ADR-0042）。

    pull worker には `sandbox ssh` が無いので、kit/steps/pr-automerge.sh を **guest の中に置いて guest の中で走らせる**
    （SB_LOCAL=1）。Mac 実機も GitHub も使わない: guest-exec の代わりに bash を通し、
    PATH の先頭には test_pr_automerge.py と同じ偽 gh だけを置く（`sandbox` は置かない = 経路が sandbox に依らないことの固定）。
    """

    def setUp(self):
        from test_pr_automerge import FAKE_GH, GATES_GREEN
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.ws = pathlib.Path(tmp.name)
        self.bin = self.ws / 'bin'; self.bin.mkdir()
        gh = self.bin / 'gh'; gh.write_text(FAKE_GH, encoding='utf-8'); gh.chmod(0o755)
        self.app = self.ws / 'app'; self.app.mkdir()
        self.guest = self.ws / 'work' / '386'; self.guest.mkdir(parents=True)
        self.calls = self.ws / 'calls.log'; self.calls.write_text('', encoding='utf-8')
        (self.guest / 'pr_url').write_text('https://github.com/akkijp-oss/aifactory/pull/1\n', encoding='utf-8')
        (self.guest / 'gates.txt').write_text(GATES_GREEN, encoding='utf-8')
        (self.guest / 'review.md').write_text('# レビュー: PASS\n\n判定の理由。\n', encoding='utf-8')
        self.env = {'PATH': f"{self.bin}:/usr/bin:/bin", 'HOME': str(self.ws), 'CALLS': str(self.calls),
                    'GH_VIEW': 'OPEN false develop sandbox/386-bug-fix', 'GH_CHECKS': 'pass ci / test',
                    'GH_MERGEABLE': 'MERGEABLE', 'GH_AFTER': 'MERGED https://github.com/akkijp-oss/aifactory/pull/1',
                    'GH_SHA': 'abc1234', 'GH_MERGE_FAILS': '',
                    'AUTOMERGE_POLL_S': '0', 'AUTOMERGE_ZERO_CHECKS_GRACE_S': '0', 'AUTOMERGE_MERGEABLE_POLL_S': '0'}
        self.timeouts = []

    def make_run(self, **auto):
        run = object.__new__(MacWaitRun)
        run.dry = False; run.keep = False; run.pj = 'aifactory'; run.task = '386'
        run.base = 'develop'; run.branch = 'sandbox/386-bug-fix'
        run.work = str(self.guest); run.env_file = run.work + '/runtime.env'
        run.project = {'worker': 'mac1', 'app_dir': str(self.app), 'repo': 'akkijp-oss/aifactory'}
        run.wf = {'steps': [{'id': 'review', 'role': 'reviewer'}, {'id': 'automerge', 'code': 'pr-automerge.sh'}]}
        run.state = {'history': []}; run.save = lambda: None
        self.logs = []; run.log = self.logs.append
        run.run_dir = self.ws / 'runs' / '2026-09-11-aifactory-386'; run.run_dir.mkdir(parents=True)
        run.set_current = lambda *a: None
        run.refresh_token = lambda: None
        run.auto_merge = {'method': 'merge', 'wait_min': 20, 'delete_branch': False, 'require_checks': True, **auto}
        run.sb = self.guest_sb(run); run.run_remote = self.guest_run_remote(run)
        return run

    def bash(self, run, cmd, input_text=None):
        return subprocess.run(['bash', '-c', run.command(cmd)], input=input_text, text=True,
                              capture_output=True, env=self.env)

    def guest_sb(self, run):
        def sb(cmd, input_text=None, check=True):
            r = self.bash(run, cmd, input_text)
            if check and r.returncode: raise RuntimeError(f'guest command failed ({r.returncode}): {r.stderr[-500:]}')
            return r.stdout
        return sb

    def guest_run_remote(self, run):
        def run_remote(cmd, log_path, render=None, timeout=3600):
            self.timeouts.append(timeout)
            r = self.bash(run, cmd)
            pathlib.Path(log_path).write_text(r.stdout + r.stderr, encoding='utf-8')
            return r.returncode, r.stdout
        return run_remote

    def gh_calls(self):
        return self.calls.read_text(encoding='utf-8')

    def test_the_kit_script_runs_inside_the_guest_and_records_the_merge(self):
        run = self.make_run()
        ok, info = run.run_code({'id': 'automerge', 'code': 'pr-automerge.sh'})
        self.assertTrue(ok, info)
        # script は guest の $WORK に置かれ、guest の中で走る（sandbox ssh は PATH にすら無い）
        self.assertTrue((self.guest / 'pr-automerge.sh').exists())
        self.assertIn('pr merge 1 --merge', self.gh_calls())
        self.assertIn('pr comment 1', self.gh_calls())
        self.assertEqual([l for l in info.splitlines() if l.strip()][-1],
                         'MERGED: abc1234 https://github.com/akkijp-oss/aifactory/pull/1')
        merged = json.loads((self.guest / 'merged.json').read_text(encoding='utf-8'))
        self.assertEqual((merged['sha'], merged['method'], merged['base']), ('abc1234', 'merge', 'develop'))
        # runner の記録（ADR-0042）。Proxmox backend と違い run_code を通らないので backend 側で呼ぶ必要がある
        self.assertEqual(run.state['merged']['sha'], 'abc1234')
        self.assertEqual(run.state['merged']['pr_url'], 'https://github.com/akkijp-oss/aifactory/pull/1')
        # guest-exec は CI 待ち（wait_min）より長く、run_remote の上限（3600 秒）は超えない
        self.assertEqual(self.timeouts, [min(3600, 20 * 60 + 900)])

    def test_the_project_setting_reaches_the_script_in_the_guest(self):
        run = self.make_run(method='squash', delete_branch=True)
        ok, info = run.run_code({'id': 'automerge', 'code': 'pr-automerge.sh'})
        self.assertTrue(ok, info)
        self.assertIn('pr merge 1 --squash', self.gh_calls())
        self.assertEqual(json.loads((self.guest / 'merged.json').read_text(encoding='utf-8'))['method'], 'squash')
        self.assertIn('run 2026-09-11-aifactory-386', self.gh_calls())      # RUN_NAME
        self.assertIn('review PASS', self.gh_calls())                       # HAS_REVIEW（workflow に reviewer がいる）

    def test_red_ci_leaves_the_pr_open_and_reports_the_reason(self):
        run = self.make_run()
        self.env['GH_CHECKS'] = 'fail ci / test'
        ok, info = run.run_code({'id': 'automerge', 'code': 'pr-automerge.sh'})
        self.assertFalse(ok)
        self.assertEqual([l for l in info.splitlines() if l.strip()][-1], 'NOMERGE: CI 赤 (ci / test)')
        self.assertNotIn('pr merge', self.gh_calls())
        self.assertNotIn('merged', run.state)

    def test_a_project_without_auto_merge_never_reaches_the_guest(self):
        """runner が工程ごと飛ばす（bin/run）。飛ばす step 名は backend とテストが同じ定数を読む"""
        self.assertIn('pr-automerge.sh', run_mod.Run.SKIPPABLE_CODE_STEPS)
