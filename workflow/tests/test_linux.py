import json
import pathlib
import sys
import tempfile
import types
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'lib'))
import linux

class LinuxBackendTest(unittest.TestCase):
    def make_run(self):
        r=object.__new__(linux.backend(object))
        r.project={'app_dir':'/var/lib/aifactory-worker/work/app','computer_use':True}
        r.root='/var/lib/aifactory-worker/work';r.task='301';r.state={'backend':'linux-pull'};r.dry=False
        r.paths('lease-1')
        r.state['history']=[];r.save=lambda:None;r.log=lambda *a:None
        r.needed_keys=lambda:['other']
        return r
    def test_paths_and_commands_are_local_linux(self):
        r=self.make_run()
        self.assertEqual(r.work,'/var/lib/aifactory-worker/work/lease-1/work/301')
        cmd=r.command('uname -s')
        self.assertNotIn('tart',cmd);self.assertNotIn('homebrew',cmd)
        self.assertIn('source /var/lib/aifactory-worker/work/lease-1/work/301/runtime.env',cmd)
    def test_computer_executable_does_not_depend_on_project_directory(self):
        r=self.make_run()
        with tempfile.TemporaryDirectory() as tmp:
            r.run_dir=pathlib.Path(tmp);sent=[];r.scp_to=lambda *args:sent.append(args)
            r.configure_computer()
            config=json.loads(sent[0][0].read_text())['mcpServers']['computer']
            self.assertEqual(config['command'],'/usr/local/lib/aifactory-computer/aifactory-computer')
            self.assertEqual(config['args'][-1],r.work)

    # ---------- code step の対応表（チケット 386）
    def test_the_code_step_table_is_inherited_from_macos(self):
        """linux-pull は macos と同じ POSIX の guest なので対応表も実装も共有する。
        ここで上書きすると、macos に足した code step が linux でだけ起動前に拒否される"""
        import macos
        self.assertNotIn('CODE_STEPS', vars(linux.backend(object)))
        self.assertEqual(linux.backend(object).CODE_STEPS, macos.backend(object).CODE_STEPS)
        self.assertEqual(linux.backend(object).CODE_STEPS['pr-automerge.sh'], 'run')

    # ---------- 再開判定（ADR-0046。チケット 391）
    def test_take_records_the_needed_purposes_before_preparing(self):
        """linux-pull の take() は MacRun.take() を継承せず丸ごと上書きしているので、
        needed_keys を自分で残さないと kb が「両方の鍵待ち」に丸め、要る用途の鍵を足しても再開しない"""
        r=self.make_run();r.resume=False;r.run_lock=object();r.lease_id='lease-1'
        r.client=types.SimpleNamespace(store=types.SimpleNamespace(workers=lambda:[]))
        with self.assertRaises(Exception):r.take()
        self.assertEqual(r.state.get('needed_keys'),['other'])

if __name__=='__main__':unittest.main()
