"""3 OS のキー名表が同じ契約であることを固定する。X11 も swiftc も pwsh も要らない。

Mac / Windows の実機はこのリポジトリの CI から触れないので、macos.swift と windows.ps1 は
ソースからキー表を抜き出して名前の集合だけを比べる。値（キーコード）の正しさは
各実装のコメントに残した公式定数名（kVK_ANSI_* / VK_OEM_* / keysym 名）で追う。
"""
import importlib.util
import os
import pathlib
import re
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]

MODIFIERS = ['CTRL', 'ALT', 'SHIFT', 'WIN', 'CMD']
SPECIALS = ['ENTER', 'TAB', 'ESC', 'SPACE', 'BACKSPACE', 'DELETE', 'LEFT', 'RIGHT', 'UP', 'DOWN',
            'HOME', 'END', 'PAGEUP', 'PAGEDOWN']
SYMBOLS = ['=', '-', '+', ',', '.', '/', ';', "'", '[', ']', '\\', '`']
CANONICAL = set(MODIFIERS + SPECIALS + SYMBOLS)
CANONICAL |= {f'F{i}' for i in range(1, 13)}
CANONICAL |= set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')


def linux_module():
    path = ROOT / 'workers/computer/linux.py'
    spec = importlib.util.spec_from_file_location('linux_key_names', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def swift_keys():
    """macos.swift の `let codes:[String:CGKeyCode]=[...]` を読む。"""
    source = (ROOT / 'workers/computer/macos.swift').read_text()
    literal = re.search(r'let codes:\[String:CGKeyCode\]=\[(.*?)\]\n', source, re.S)
    assert literal, 'macos.swift のキー表が見つからない'
    pairs = re.findall(r'"((?:[^"\\]|\\.)*)"\s*:\s*(\d+)', literal.group(1))
    return {name.replace('\\\\', '\\').replace('\\"', '"'): int(code) for name, code in pairs}


def powershell_keys():
    """windows.ps1 の `$keys=@{...}` と、そのあとに足す A-Z0-9 を読む。"""
    source = (ROOT / 'workers/computer/windows.ps1').read_text()
    literal = re.search(r'\$keys=@\{(.*?)\}\n', source, re.S)
    assert literal, 'windows.ps1 のキー表が見つからない'
    keys = {}
    for name, code in re.findall(r"([A-Za-z0-9]+|'(?:[^']|'')*'|\"[^\"]*\")\s*=\s*(0x[0-9A-Fa-f]+|\d+)",
                                 literal.group(1)):
        if name[0] in '\'"':
            name = name[1:-1].replace("''", "'")
        keys[name] = int(code, 0)
    letters = re.search(r"foreach\(\$c in \[char\[\]\]'([A-Z0-9]+)'\)", source)
    assert letters, 'windows.ps1 の A-Z0-9 の補完が見つからない'
    keys.update({c: ord(c) for c in letters.group(1)})
    return keys


class KeyNameContractTest(unittest.TestCase):
    def test_three_operating_systems_share_one_key_name_set(self):
        for name, keys in (('linux', linux_module().KEYS), ('macos', swift_keys()), ('windows', powershell_keys())):
            with self.subTest(os=name):
                self.assertEqual(set(keys), CANONICAL)

    def test_reported_failing_combination_is_accepted_everywhere(self):
        # チケット 344: {"action":"key","keys":["CMD","SHIFT","="]} が unsupported key だった。
        for name, keys in (('linux', linux_module().KEYS), ('macos', swift_keys()), ('windows', powershell_keys())):
            with self.subTest(os=name):
                for key in ('CMD', 'SHIFT', '=', 'F5', 'ESC', '-'):
                    self.assertIn(key, keys)

    def test_key_codes_are_distinct_except_the_documented_aliases(self):
        # 重複してよいのは 2 つだけ: '+' は '=' と同じコード + SHIFT、'WIN' は 'CMD' の別名。
        for name, keys in (('macos', swift_keys()), ('windows', powershell_keys())):
            with self.subTest(os=name):
                self.assertEqual(keys['+'], keys['='])
                self.assertEqual(keys['WIN'], keys['CMD'])
                codes = [v for k, v in keys.items() if k not in ('+', 'WIN')]
                self.assertEqual(len(codes), len(set(codes)))

    def test_unsupported_name_error_lists_the_accepted_names(self):
        module = linux_module()
        with patch.dict(os.environ, {'DISPLAY': ':99'}, clear=True):
            with self.assertRaises(ValueError) as caught:
                module.native({'action': 'key', 'keys': ['exec sh']})
        message = str(caught.exception)
        self.assertIn('exec sh', message)
        for key in ('=', 'F5', 'ESC', 'CMD'):
            self.assertIn(key, message)

    def test_tool_descriptions_name_the_supported_keys(self):
        for path, start, end in (('workers/cmd/aifactory-computer/main.go', '"description": "Operate', '", "inputSchema"'),
                                 ('console/bin/mcp', '("computer_action", "', ', schema(')):
            with self.subTest(file=path):
                source = (ROOT / path).read_text()
                head = source.index(start)
                description = source[head:source.index(end, head)]
                for key in ('=', 'F1', 'ESC', 'CMD'):
                    self.assertIn(key, description)


if __name__ == '__main__':
    unittest.main()
