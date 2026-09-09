import pathlib,sys,tempfile,unittest,types,json
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'lib'))
from computer import validate,Desktop
from pull import Error

class ComputerTests(unittest.TestCase):
 def test_unbounded_or_arbitrary_commands_rejected(self):
  for r in [{'action':'shell','command':'whoami'},{'action':'click','x':-1,'y':4},{'action':'click','x':True,'y':4},{'action':'key','keys':[]},{'action':'type','text':'x'*8193},{'action':'scroll','amount':21},{'action':'screenshot','host':'other'}]:
   with self.assertRaises(Error):validate(r)
 def test_symbol_and_function_keys_pass_validation(self):
  # チケット 344 の失敗例。名前の可否は各 OS のヘルパーが決め、ここは個数と長さだけ見る。
  for keys in (['CMD','SHIFT','='],['CTRL','-'],['F5'],['ESC'],['\\'],['`']):
   self.assertEqual(validate({'action':'key','keys':keys})['keys'],keys)
  for keys in (['x'*13],[],['A','B','C','D','E'],[1]):
   with self.assertRaises(Error):validate({'action':'key','keys':keys})
 def test_unicode_text_supported(self):self.assertEqual(validate({'action':'type','text':'日本語🙂'})['text'],'日本語🙂')
 def test_session_paths_cannot_escape(self):
  d=Desktop(root='/tmp/computer-test')
  for s in ['../escape','desktop-../../x','/etc/passwd','other-1',None]:
   with self.assertRaises(Error):d.directory(s)
 def test_closed_session_cannot_operate(self):
  with tempfile.TemporaryDirectory() as tmp:
   d=Desktop(root=tmp);p=d.directory('desktop-test');p.mkdir();d.save(p,{'session':'desktop-test','status':'closed'})
   with self.assertRaises(Error):d.load('desktop-test')
 def test_same_session_lock_is_required_for_action(self):
  import fcntl
  with tempfile.TemporaryDirectory() as tmp:
   d=Desktop(root=tmp);p=pathlib.Path(tmp);d.load=lambda _:(p,{'status':'ready'},None)
   with (p/'action.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    with self.assertRaisesRegex(Error,'busy'):d.action('desktop-test',{'action':'screenshot'})

if __name__=='__main__':unittest.main()
