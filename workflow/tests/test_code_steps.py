"""kit/workflows/*.yml の code step は、pull backend の対応表に必ず分類されている（チケット 386）。

  python3 -m unittest discover -s workflow/tests -p 'test_code_steps.py' -v

ADR-0042 で automerge step を全 workflow に足したとき、pull backend（macos / windows / linux）の対応表を
更新し忘れたので、macos-pull の PJ が run を 1 つも始められなくなった（asura #381）。
再発を防ぐため「新しい code step を足して対応表を更新していない」をここで赤にする。

分類は run（backend が実装している）/ noop（対応しないが素通りさせる）/ unsupported（起動前に拒否する）の 3 つ。
unsupported のままでも構わない（Windows の pr-automerge.sh のように、実機で確かめられない経路は
増やさずに起動前へ倒す判断）。分類そのものを忘れることだけを禁じる。
"""
import importlib.machinery
import importlib.util
import pathlib
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'workflow' / 'lib'))
import linux
import macos
import windows

CLASSES = {'run', 'noop', 'unsupported'}
BACKENDS = {'macos': macos, 'windows': windows, 'linux': linux}


def workflow_code_steps():
    steps = {}
    for f in sorted((ROOT / 'workflow' / 'kit' / 'workflows').glob('*.yml')):
        for s in yaml.safe_load(f.read_text(encoding='utf-8'))['steps']:
            if 'code' in s: steps.setdefault(s['code'], []).append(f.name)
    return steps


def table(name):
    return BACKENDS[name].backend(object).CODE_STEPS


class CodeStepTableTest(unittest.TestCase):
    def test_every_workflow_code_step_is_classified_by_every_pull_backend(self):
        steps = workflow_code_steps()
        self.assertIn('pr-automerge.sh', steps)        # ADR-0042 の step が消えていない
        for name in BACKENDS:
            got = table(name)
            missing = sorted(set(steps) - set(got))
            self.assertEqual(missing, [], f'{name}.py の CODE_STEPS に無い code step: {missing}'
                                          f'（kit/steps に足したら 3 つの pull backend の表も更新すること）')
            for step, how in sorted(got.items()):
                self.assertIn(how, CLASSES, f'{name}.py の {step} の分類が不正: {how}')

    def test_the_table_does_not_keep_steps_no_workflow_uses(self):
        steps = workflow_code_steps()
        for name in BACKENDS:
            stale = sorted(set(table(name)) - set(steps))
            self.assertEqual(stale, [], f'{name}.py の CODE_STEPS に残った使われていない step: {stale}')

    def test_a_step_that_is_not_implemented_is_refused_before_the_run_starts(self):
        """分類が run / noop でも「runner が飛ばす」でもない step は、起動前に ValueError で拒否される。
        黙って PR を作らずに終わる（= 人が気づけない）経路を作らない"""
        skippable = set(runner_module().Run.SKIPPABLE_CODE_STEPS)
        for name in BACKENDS:
            got = table(name)
            for step, how in sorted(got.items()):
                if how != 'unsupported': continue
                with self.subTest(backend=name, step=step):
                    Run = BACKENDS[name].backend(base_class())
                    if step in skippable:
                        Run({'steps': [{'code': step}]})                     # auto_merge 無し = runner が飛ばす
                        with self.assertRaises(ValueError): Run({'steps': [{'code': step}]}, auto_merge={'method': 'merge'})
                    else:
                        with self.assertRaises(ValueError): Run({'steps': [{'code': step}]})

    def test_windows_declares_its_own_table_instead_of_inheriting_macos(self):
        """windows.py の表は macos の表を取り込まない（自分で宣言する）。
        取り込むと macos に足した step が分類ごと Windows へ流れ込み、Windows のゲスト（PowerShell）で
        動く保証が無いまま「分類済み」になる。上の「表の更新忘れ」の検出もそこだけ素通りして、
        run を使い切った最後の工程で落ちる（ADR-0059 が採らなかった案そのもの。チケット 386）"""
        real = windows.pull_backend

        def macos_with_a_new_step(Run):
            Pull = real(Run)
            Pull.CODE_STEPS = {**Pull.CODE_STEPS, 'brand-new.sh': 'run'}
            return Pull

        windows.pull_backend = macos_with_a_new_step
        try:
            got = windows.backend(object).CODE_STEPS
        finally:
            windows.pull_backend = real
        self.assertNotIn('brand-new.sh', got,
                         'windows.py の CODE_STEPS は macos の表を spread せず、自分で書くこと')

    def test_a_new_code_step_nobody_classified_is_refused(self):
        for name in BACKENDS:
            with self.subTest(backend=name):
                Run = BACKENDS[name].backend(base_class())
                with self.assertRaises(ValueError): Run({'steps': [{'code': 'brand-new.sh'}]})


def base_class():
    class Base:
        def __init__(self, wf, auto_merge=None):
            self.wf = wf
            self.project = {'app_dir': 'C:/work/app', 'worker': 'w1'}
            self.task = '386'; self.resume = False; self.state = {}; self.auto_merge = auto_merge
    return Base


def runner_module():
    spec = importlib.util.spec_from_loader('code_steps_run', importlib.machinery.SourceFileLoader(
        'code_steps_run', str(ROOT / 'workflow/bin/run')))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


if __name__ == '__main__': unittest.main()
