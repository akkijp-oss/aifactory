import json
import pathlib
import sys
import tempfile
import types
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'lib'))
import windows

class WindowsBackendTest(unittest.TestCase):
    def make_run(self):
        r=object.__new__(windows.backend(object))
        r.dry=False;r.keep=False;r.work='C:/work/lease/work/210';r.env_file=r.work+'/runtime.env'
        r.project={'app_dir':'C:/work/lease/app','gates':'gates.ps1'}
        r.project_dir=pathlib.Path('/project');r.base='main';r.state={'history':[]}
        r.run_dir=pathlib.Path('/run');r.set_current=lambda *a:None
        r.log=lambda *a:None;r.save=lambda:None
        return r

    def make_release_run(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        r=self.make_run();r.run_dir=pathlib.Path(tmp.name)
        r.project['worker']='win1';r.client=types.SimpleNamespace(lease='lease-1')
        r.client.store=types.SimpleNamespace(release_lease=lambda *a:None)
        return r
    def test_powershell_quoting(self):
        self.assertEqual(windows.quote("a'; $(whoami)"),"'a''; $(whoami)'")
        with self.assertRaises(ValueError):windows.quote('a\0b')
    def test_credentials_are_transferred_only_as_stdin(self):
        r=self.make_run();r.credentials=lambda:{'GH_TOKEN':'private-value'}
        calls=[];r.sb=lambda script,**kw:calls.append((script,kw))
        r.refresh_token()
        self.assertNotIn('private-value',calls[0][0])
        self.assertIn('input_text',calls[0][1])
    def test_nonzero_gate_without_fail_line_fails(self):
        r=self.make_run();r.scp_to=lambda *a:None;r.write_remote=lambda *a:None
        r.run_remote=lambda *a:(7,'tool crashed\n')
        self.assertEqual(r.run_code({'id':'gates','code':'gates.sh'}),(False,'tool crashed\n'))
    def test_native_command_uses_prompt_pipe_and_quoted_model(self):
        r=self.make_run();cmd=r.agent_command("C:/work/a'b.md",'sonnet',1)
        self.assertIn("ReadAllText('C:/work/a''b.md') |",cmd)
        self.assertIn('exit $LASTEXITCODE',cmd)
        self.assertNotIn('timeout ',cmd)
    def test_computer_mcp_command_is_opt_in(self):
        r=self.make_run()
        self.assertNotIn('--mcp-config',r.agent_command('prompt.md','sonnet',1))
        r.project['computer_use']=True
        cmd=r.agent_command('prompt.md','sonnet',1)
        self.assertIn('--strict-mcp-config',cmd)
        self.assertIn("--mcp-config 'C:/work/lease/work/210/computer-mcp.json'",cmd)

    def test_run_paths_isolate_lease(self):
        r=self.make_run();r.root='C:/work';r.task='210';r.paths('lease-2')
        self.assertEqual(r.project['app_dir'],'C:/work/lease-2/app')
        self.assertEqual(r.env_file,'C:/work/lease-2/work/210/runtime.env')

    def test_collect_skips_non_regular_entries_instead_of_throwing(self):
        # チケット 277: throw するとゲストのコマンドが非 0 で終わり、release が予約を保持したまま止まる
        r=self.make_run();captured=[]
        r.sb=lambda script,**kw:(captured.append(script),'{}')[1]
        r.collect()
        script=captured[0]
        self.assertNotIn('throw',script)
        for token in ("reason='directory'","reason='symlink'","reason='size'"):
            with self.subTest(token=token):self.assertIn(token,script)
        self.assertIn('PSIsContainer',script)
        self.assertIn('ReparsePoint',script)
        self.assertIn('4194304',script)
        self.assertIn('files=$out',script)
        self.assertIn('skipped=@($skipped)',script)

    def test_skipped_artifacts_are_recorded_and_workspace_released(self):
        r=self.make_release_run()
        r.sb=lambda *a,**kw:json.dumps({'files':{},'skipped':[{'name':'shots','reason':'directory'},
                                                              {'name':'junction','reason':'symlink'}]})
        released=[]
        r.client.execute=lambda kind:(released.append(kind),('op',types.SimpleNamespace(returncode=0)))[1]
        r.release()
        self.assertEqual(released,['guest-release'])
        self.assertTrue(r.state['released'])
        self.assertEqual([x['name'] for x in r.state['artifacts_skipped']],['shots','junction'])

    def test_single_skip_collapsed_by_convertto_json_is_still_recorded(self):
        # ConvertTo-Json は 1 要素の配列を単体オブジェクトに潰すことがある
        r=self.make_release_run()
        r.sb=lambda *a,**kw:json.dumps({'files':{},'skipped':{'name':'shots','reason':'directory'}})
        r.client.execute=lambda kind:('op',types.SimpleNamespace(returncode=0))
        r.release()
        self.assertEqual(r.state['artifacts_skipped'],[{'name':'shots','reason':'directory'}])

    def test_every_credential_reaches_the_guest_not_just_two_names(self):
        """guest の環境には runtime.env の全 key を入れる（チケット 391）。GH_TOKEN と CLAUDE_CODE_OAUTH_TOKEN の
        2 つ決め打ちだったので、鍵プールの系統別の鍵（_FABLE / _OPUS / …）と CLAUDE_KEY_NAME_* が guest に届かず、
        全モデルが同じ 1 本で動き、起動も LAUNCHES に数えられなかった"""
        cmd=self.make_run().command('claude -p x')
        self.assertIn('PSObject.Properties',cmd)
        self.assertIn('Set-Item',cmd)
        self.assertNotIn('$credentials.GH_TOKEN',cmd)              # 決め打ちで写さない（増えた key が黙って落ちる）
        self.assertNotIn('$credentials.CLAUDE_CODE_OAUTH_TOKEN',cmd)

    def test_credentials_are_asked_for_every_refresh(self):
        """工程ごとに鍵を選び直す（無効化した鍵が次の工程で入れ替わる。ADR-0046）"""
        r=self.make_run();asked=[]
        r.credentials=lambda:(asked.append(1),{'GH_TOKEN':'g','CLAUDE_KEY_NAME_OPUS':'pool-a'})[1]
        written=[];r.write_remote=lambda remote,data:written.append((remote,data))
        r.refresh_token();r.refresh_token()
        self.assertEqual(len(asked),2)
        self.assertEqual(json.loads(written[-1][1])['CLAUDE_KEY_NAME_OPUS'],'pool-a')
        self.assertEqual(written[-1][0],r.env_file)

if __name__=='__main__':unittest.main()
