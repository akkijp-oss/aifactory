import pathlib
import sys
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

if __name__=='__main__':unittest.main()
