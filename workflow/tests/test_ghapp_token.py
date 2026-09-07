"""Regression: an App's declared permissions can exceed an installation's grant."""
import pathlib
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('jq'), 'jq required by sandbox CLI')
class GitHubTokenTest(unittest.TestCase):
    def functions(self):
        text=(pathlib.Path(__file__).resolve().parents[2]/'sandbox/bin/sandbox').read_text()
        return text[text.index('ghapp_token() {'):text.index('# resolve_gh_token')]

    def test_token_uses_installation_permissions_not_app_declaration(self):
        script='''set -euo pipefail
ghapp_api() {
 case "$1 $2" in
  'GET /repos/example/project/installation') printf '%s' '{"id":123,"permissions":{"contents":"write","pull_requests":"write","metadata":"read"}}' ;;
  'GET /app') echo 'must not request declared permissions' >&2; return 1 ;;
  'POST /app/installations/123/access_tokens')
   jq -e '.permissions == {"contents":"write","pull_requests":"write","metadata":"read"} and .repositories == ["project"]' <<< "$3" >/dev/null
   printf '%s' '{"token":"ghs_test_value"}' ;;
  *) return 1 ;;
 esac
}
'''+self.functions()+"\nghapp_token example/project\n"
        r=subprocess.run(['bash','-c',script],text=True,capture_output=True)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(r.stdout.strip(),'ghs_test_value')

    def test_empty_token_is_an_error(self):
        script='''set -euo pipefail
ghapp_api() {
 if [[ "$1" == GET ]]; then echo '{"id":123,"permissions":{}}'; else echo '{"message":"denied"}'; fi
}
'''+self.functions()+"\nghapp_token example/project\n"
        r=subprocess.run(['bash','-c',script],text=True,capture_output=True)
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(r.stdout,'')
        self.assertIn('denied',r.stderr)
