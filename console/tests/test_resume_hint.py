import importlib.util,pathlib,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('resume_core',ROOT/'console/lib/core.py')
core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
class ResumeHintTest(unittest.TestCase):
    def test_resume_points_to_recorded_run(self):
        ticket={'id':101,'pj':'example-mac','run':'2026-09-06-example-mac-101'}
        with patch.object(core,'rows',return_value=[ticket]),patch.object(core.JobStore,'start',return_value={}) as start:
            core.ticket_run(101,{'resume':True})
            self.assertEqual(start.call_args.kwargs['run_hint'],ticket['run'])
            self.assertIn('--resume',start.call_args.args[1])
            core.ticket_run(101,{'resume':True,'dry_run':True})
            self.assertTrue(start.call_args.kwargs['run_hint'].endswith('-dry'))
