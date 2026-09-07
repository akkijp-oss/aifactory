"""Desktop responses must remain native MCP images, including tool errors."""
import importlib.machinery
import importlib.util
import json
import pathlib
import types
import unittest
from unittest.mock import patch

path=pathlib.Path(__file__).resolve().parents[1]/'bin/mcp'
loader=importlib.machinery.SourceFileLoader('computer_mcp_test_module',str(path))
spec=importlib.util.spec_from_loader(loader.name,loader)
mcp=importlib.util.module_from_spec(spec);loader.exec_module(mcp)

class ComputerMcpTests(unittest.TestCase):
    def test_image_is_not_wrapped_in_json_text(self):
        calls=[]
        def action(session,request):
            calls.append((session,request))
            return {'ok':True,'image':'cG5n','path':'/test/proof.png','sha256':'digest'}
        with patch.object(mcp,'desktop_api',return_value=types.SimpleNamespace(action=action)):
            result=mcp.handle({'method':'tools/call','params':{'name':'computer_action','arguments':{'session':'desktop-test','action':'screenshot'}}})
        self.assertFalse(result['isError'])
        self.assertEqual(calls,[('desktop-test',{'action':'screenshot'})])
        image=next(c for c in result['content'] if c['type']=='image')
        self.assertEqual(image,{'type':'image','mimeType':'image/png','data':'cG5n'})
        metadata=json.loads(result['content'][0]['text'])
        self.assertNotIn('image',metadata)
        self.assertEqual(metadata['sha256'],'digest')

    def test_desktop_failure_is_tool_error(self):
        with patch.object(mcp,'desktop_api',side_effect=RuntimeError('desktop unavailable')):
            result=mcp.handle({'method':'tools/call','params':{'name':'computer_action','arguments':{'session':'desktop-test','action':'screenshot'}}})
        self.assertTrue(result['isError'])
        self.assertEqual([c['type'] for c in result['content']],['text'])
        self.assertIn('desktop unavailable',result['content'][0]['text'])

if __name__=='__main__':unittest.main()
