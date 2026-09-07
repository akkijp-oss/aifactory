import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('bootstrap',Path(__file__).resolve().parents[1]/'bootstrap/install.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)

class BootstrapTest(unittest.TestCase):
    def env(self,**extra):
        return dict(AIFACTORY_URL='https://ctl.example:8766',AIFACTORY_WORKER='linux-01',AIFACTORY_TOKEN='s'*48,**extra)
    def test_rejects_credentials_and_ambiguous_endpoint(self):
        for u in ('http://ctl.example','https://u:p@ctl.example','https://ctl.example/path','https://ctl.example/?secret=x'):
            e=self.env();e['AIFACTORY_URL']=u
            with self.assertRaises(ValueError):b.settings(e)
    def test_invalid_secret_error_does_not_echo_secret(self):
        e=self.env();e['AIFACTORY_TOKEN']='not-a-valid-secret'
        with self.assertRaises(ValueError) as error:b.settings(e)
        self.assertNotIn(e['AIFACTORY_TOKEN'],str(error.exception))
    def test_ca_and_ip_validation(self):
        with self.assertRaises(ValueError):b.settings(self.env(AIFACTORY_CA_B64='abc',AIFACTORY_CA_FILE='/tmp/ca'))
        with self.assertRaises(ValueError):b.settings(self.env(AIFACTORY_SERVER_IP='127.0.0.1\nother'))
        with self.assertRaises(ValueError):b.certificate(b.settings(self.env(AIFACTORY_CA_B64=base64.b64encode(b'not a CA').decode())))
    def test_refuses_other_worker_and_active_lease(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';old={'worker':'linux-01','url':'https://ctl.example:8766','state_dir':d};p.write_text(json.dumps(old))
            self.assertEqual(b.existing_config(p,b.settings(self.env())),old)
            (Path(d)/'guest-lease').write_text('owned')
            with self.assertRaisesRegex(ValueError,'active lease'):b.existing_config(p,b.settings(self.env()))
            old['worker']='other';p.write_text(json.dumps(old))
            with self.assertRaisesRegex(ValueError,'another worker'):b.existing_config(p,b.settings(self.env()))
    def test_private_write_refuses_symlink_and_protects_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'token';b.private_write(p,b'secret')
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            link=Path(d)/'link';link.symlink_to(p)
            with self.assertRaises(ValueError):b.private_write(link,b'replacement')
            self.assertEqual(p.read_bytes(),b'secret')

if __name__=='__main__':unittest.main()
