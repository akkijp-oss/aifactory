"""Run with AIFACTORY_X11_TEST=1 xvfb-run -a python3 -m unittest ..."""
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

class LinuxDesktopGuardTest(unittest.TestCase):
    def test_wayland_is_rejected_before_using_xwayland(self):
        path=pathlib.Path(__file__).resolve().parents[1]/'computer/linux.py'
        spec=importlib.util.spec_from_file_location('linux_desktop_guard',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for extra in ({'WAYLAND_DISPLAY':'wayland-0'},{'XDG_SESSION_TYPE':'wayland'}):
            with patch.dict(os.environ,{'DISPLAY':':99',**extra},clear=True):
                with self.assertRaisesRegex(ValueError,'Wayland'):
                    module.native({'action':'screenshot'})

@unittest.skipUnless(os.environ.get('AIFACTORY_X11_TEST')=='1','requires a dedicated Xvfb display')
class LinuxDesktopTest(unittest.TestCase):
    def test_unicode_multiline_capture_and_coordinates(self):
        path=pathlib.Path(__file__).resolve().parents[1]/'computer/linux.py'
        spec=importlib.util.spec_from_file_location('linux_desktop',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            output=pathlib.Path(tmp)/'actual.txt'
            script="""import tkinter as tk,sys,pathlib
r=tk.Tk();r.geometry('800x600+0+0');t=tk.Text(r);t.pack(fill='both',expand=True);t.focus_force()
def save():
 pathlib.Path(sys.argv[1]).write_text(t.get('1.0','end-1c'));r.after(50,save)
r.after(50,save);r.mainloop()
"""
            process=subprocess.Popen([sys.executable,'-c',script,str(output)])
            try:
                deadline=time.monotonic()+10
                while not output.exists() and time.monotonic()<deadline:time.sleep(.05)
                self.assertTrue(output.exists(),'test editor failed to start')
                shot=module.native({'action':'screenshot'})
                self.assertEqual(shot['mimeType'],'image/png');self.assertLessEqual(shot['width'],1024)
                module.native({'action':'click','x':100,'y':100})
                # Repeating a click at the current position must not wait for motion.
                started=time.monotonic()
                module.native({'action':'click','x':100,'y':100})
                self.assertLess(time.monotonic()-started,3)
                text='日本語の入力テスト\n'+'\n'.join(f'行{i}: abcDEF 123' for i in range(40))
                module.native({'action':'type','text':text})
                deadline=time.monotonic()+5
                while output.read_text()!=text and time.monotonic()<deadline:time.sleep(.05)
                self.assertEqual(output.read_text(),text)
                module.native({'action':'key','keys':['CTRL','HOME']})
                module.native({'action':'scroll','amount':-3})
                self.assertTrue(module.native({'action':'screenshot'})['ok'])
                with self.assertRaises(ValueError):module.native({'action':'click','x':-1,'y':0})
                with self.assertRaises(ValueError):module.native({'action':'key','keys':['exec sh']})
            finally:
                process.terminate();process.wait(timeout=5)

if __name__=='__main__':unittest.main()
