import base64
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('macos_backend', ROOT/'workflow/lib/macos.py')
macos=importlib.util.module_from_spec(spec);spec.loader.exec_module(macos)


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


if __name__=='__main__':unittest.main()
