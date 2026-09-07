import importlib.machinery,importlib.util,os,pathlib,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_loader('resume_run',importlib.machinery.SourceFileLoader('resume_run',str(ROOT/'workflow/bin/run')))
run=importlib.util.module_from_spec(spec);spec.loader.exec_module(run)
class ResumeRunTest(unittest.TestCase):
    def test_cross_day_resume_uses_recorded_run(self):
        with patch.dict(os.environ,{'AIFACTORY_RESUME_RUN':'2026-09-06-example-mac-101'}):
            self.assertEqual(run.run_name('example-mac','101',resume=True),'2026-09-06-example-mac-101')
            self.assertNotEqual(run.run_name('example-mac','101',dry=True,resume=True),'2026-09-06-example-mac-101')
    def test_resume_cannot_select_path_or_another_ticket(self):
        for name in ['../2026-09-06-example-mac-101','2026-09-06-example-mac-102','2026-09-06-other-101']:
            with patch.dict(os.environ,{'AIFACTORY_RESUME_RUN':name}):
                with self.assertRaises(ValueError):run.run_name('example-mac','101',resume=True)
