import json
import pathlib
import sys
import tempfile
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'lib'))
import linux

class LinuxBackendTest(unittest.TestCase):
    def make_run(self):
        r=object.__new__(linux.backend(object))
        r.project={'app_dir':'/var/lib/aifactory-worker/work/app','computer_use':True}
        r.root='/var/lib/aifactory-worker/work';r.task='301';r.state={'backend':'linux-pull'};r.dry=False
        r.paths('lease-1')
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

if __name__=='__main__':unittest.main()
