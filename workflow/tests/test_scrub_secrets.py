"""kit/steps/scrub.sh: PR 本文・PR コメント・gates の転記に載る文章から既知の秘密の形を伏せる（2026-09-09 run 358 の事故から）。

テスト出力に VM の env の値（CLAUDE_CODE_OAUTH_TOKEN_OPUS=sk-ant-…）が混ざり、gates.sh の base 確認の転記 → gates.txt →
pr-create.sh の PR 本文、という経路で公開 PR に載る寸前だった。伏せる判定は scrub.sh 1 か所に置き、呼ぶ側はそこに通すだけ。
"""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRUB = ROOT / 'workflow/kit/steps/scrub.sh'
STEPS = ROOT / 'workflow/kit/steps'


def scrub(text):
    r = subprocess.run(['bash', str(SCRUB)], input=text, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


class ScrubTest(unittest.TestCase):
    # 本物に似た文字列をソースに書かない（bin/oss-check.sh が弾く）。長い本体はテストの中で組み立てる
    CLAUDE = 'sk-ant-' + 'oat01-' + 'AbCd' + 'x' * 40
    GHS = 'ghs_' + 'ABCD' + 'Z' * 30
    GHP = 'ghp_' + 'abcd' + 'z' * 30
    PAT = 'github_pat_' + '11AA' + 'b' * 30

    def test_claude_long_lived_tokens_are_masked(self):
        out = scrub(f'CLAUDE_CODE_OAUTH_TOKEN_OPUS={self.CLAUDE}\n' + f"'{self.CLAUDE}'\n")
        self.assertNotIn('x' * 10, out)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN_OPUS=****', out)
        self.assertIn("'sk-ant-oat01-AbCd****'", out)

    def test_github_tokens_are_masked(self):
        out = scrub(f'GH_TOKEN={self.GHS} {self.GHP} {self.PAT}\n')
        self.assertNotIn('Z' * 10, out); self.assertNotIn('z' * 10, out); self.assertNotIn('b' * 10, out)
        self.assertIn('GH_TOKEN=****', out)
        self.assertIn('ghp_abcd****', out)
        self.assertIn('github_pat_11AA****', out)

    def test_short_dummies_and_ordinary_lines_are_kept(self):
        """テストの偽物（sk-ant-oat01-ambient）や普通の行は変えない。行数も変えない"""
        text = 'PASS bash-n\nFAIL unittest-workflow (~/gates/unittest-workflow.log)\nt.Setenv(OAuthTokenEnv, "sk-ant-oat01-ambient")\nghs_short\n'
        self.assertEqual(scrub(text), text)

    def test_pr_steps_and_gates_go_through_scrub(self):
        """PR 本文・PR コメント・gates の転記は scrub.sh を通す（呼び忘れを検査する）"""
        for name, needle in (('pr-create.sh', 'scrub <<EOF | sb "cat > $WORK/pr-body.md"'),
                             ('pr-merge.sh', 'scrub <<EOF | sb "cat > $WORK/merge-comment.md"'),
                             ('gates.sh', '| bash "$SCRUB" > "$tmp/gates.txt"')):
            self.assertIn(needle, (STEPS / name).read_text(encoding='utf-8'), name)
        self.assertIn('bash "$SCRUB" < "$tmp/excerpt-$g.log"', (STEPS / 'gates.sh').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
