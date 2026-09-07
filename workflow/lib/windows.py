"""Native PowerShell workflow in a dedicated Windows pull-worker VM."""
import base64
import fcntl
import json
import os
import pathlib
import re
import uuid
from macos import backend as pull_backend, Client


def quote(value):
    if '\0' in str(value): raise ValueError('NUL in PowerShell value')
    return "'" + str(value).replace("'", "''") + "'"


def backend(Run):
    class WindowsRun(pull_backend(Run)):
        backend_label = 'Windows workspace'

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.project = dict(self.project)
            self.root = str(pathlib.PureWindowsPath(self.project['app_dir']).parent).replace('\\', '/')
            self.state['backend'] = 'windows-pull'
            self.paths(self.state.get('lease', 'dry-run'))

        def paths(self, lease):
            self.project['app_dir'] = f'{self.root}/{lease}/app'
            self.work = f'{self.root}/{lease}/work/{self.task}'
            self.env_file = self.work + '/runtime.env'

        def command(self, cmd):
            return (f"$env:SANDBOX_APP_DIR={quote(self.project['app_dir'])}; "
                    "$env:PATH=[Environment]::GetEnvironmentVariable('PATH','Machine'); "
                    "$env:CLAUDE_CODE_GIT_BASH_PATH='C:/Program Files/Git/bin/bash.exe'; "
                    f"if (Test-Path -LiteralPath {quote(self.env_file)}) {{ "
                    f"$credentials=([IO.File]::ReadAllText({quote(self.env_file)}) | ConvertFrom-Json); "
                    "$env:GH_TOKEN=$credentials.GH_TOKEN; $env:CLAUDE_CODE_OAUTH_TOKEN=$credentials.CLAUDE_CODE_OAUTH_TOKEN }; " + cmd)

        def write_remote(self, remote, data):
            if self.dry: return
            if isinstance(data, str): data = data.encode('utf-8')
            if len(data) > 350000: raise RuntimeError('workflow input exceeds transfer limit')
            self.sb(f"$p={quote(remote)}; [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($p)) | Out-Null; "
                    "[IO.File]::WriteAllBytes($p,[Convert]::FromBase64String([Console]::In.ReadToEnd()))",
                    input_text=base64.b64encode(data).decode())

        def scp_to(self, local, remote):
            self.write_remote(remote, pathlib.Path(local).read_bytes())

        def vm_read(self, rel):
            return self.sb(f"$p={quote(self.work + '/' + rel)}; if(Test-Path -LiteralPath $p -PathType Leaf) {{ [Console]::Write([IO.File]::ReadAllText($p)) }}")

        def refresh_token(self):
            if not self.dry: self.write_remote(self.env_file, json.dumps(self.credentials()))

        def take(self):
            if self.dry: return
            import aifactory_paths as paths
            self.run_lock = open(self.run_dir / 'windows.lock', 'a')
            fcntl.flock(self.run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lease = self.state.get('lease') if self.resume else f'run-{self.task}-{uuid.uuid4().hex[:16]}'
            if not lease: raise RuntimeError('Windows resume has no recorded lease')
            self.paths(lease)
            self.client = Client(os.environ.get('AIFACTORY_WORKER_DB') or paths.WORKSPACE / 'workers' / 'queue.sqlite3',
                                 self.project['worker'], lease, self.run_dir)
            worker = next((w for w in self.client.store.workers() if w['id'] == self.project['worker']), {})
            info = worker.get('info', {})
            if not worker.get('online') or info.get('os') != 'windows' or not info.get('lifecycle'):
                raise RuntimeError('Windows worker is offline or not configured')
            if pathlib.PureWindowsPath(info.get('work_root', '')) != pathlib.PureWindowsPath(self.root):
                raise RuntimeError('project app_dir must be <worker work_root>/app')
            if self.resume:
                if (worker.get('lease') or {}).get('id') != lease: raise RuntimeError('resume requires this run’s retained lease')
                ready = self.sb(f"if ((Test-Path -LiteralPath ($env:SANDBOX_APP_DIR+'/.git')) -and (Test-Path -LiteralPath {quote(self.work+'/ticket.md')})) {{ 'prepared' }}").strip()
                if ready != 'prepared': raise RuntimeError('incomplete Windows setup; inspect retained workspace before recovery')
                self.refresh_token()
                return
            self.client.store.acquire(self.project['worker'], lease)
            self.state['lease'] = lease; self.save()
            self.set_current('prepare', 'code', 'prepare.log')
            _, result = self.client.execute('guest-prepare')
            if result.returncode: raise RuntimeError('Windows workspace preparation failed; lease retained')
            self.setup_project()

        def setup_project(self):
            self.sb(f"[IO.Directory]::CreateDirectory({quote(self.work)}) | Out-Null")
            provision = self.project_dir / 'provision.ps1'
            if provision.exists():
                remote = self.work + '/provision.ps1'
                self.scp_to(provision, remote)
                rc, out = self.run_remote(f"& {quote(remote)}; exit $LASTEXITCODE", self.run_dir / 'code-prepare.log')
                if rc: raise RuntimeError(f'Windows provisioning failed ({rc}): {out[-1000:]}')
            self.refresh_token()
            ref = self.branch if self.pr_number else self.base
            self.sb(f"if(Test-Path -LiteralPath $env:SANDBOX_APP_DIR) {{ throw 'app already exists' }}; "
                    f"gh auth setup-git; if($LASTEXITCODE){{exit $LASTEXITCODE}}; git clone -q {quote('https://github.com/'+self.project['repo']+'.git')} $env:SANDBOX_APP_DIR; if($LASTEXITCODE){{exit $LASTEXITCODE}}; "
                    f"Set-Location $env:SANDBOX_APP_DIR; git fetch -q origin {quote(ref)}; if($LASTEXITCODE){{exit $LASTEXITCODE}}; "
                    f"git checkout -q -b {quote(self.branch)} {quote('origin/'+ref)}; if($LASTEXITCODE){{exit $LASTEXITCODE}}; "
                    "git config user.name aifactory; git config user.email aifactory@users.noreply.github.com; exit $LASTEXITCODE")
            self.write_remote(self.work + '/ticket.md', self.ticket)
            self.log('Windows workspace ready')

        def diff_summary(self):
            return self.sb(f"Set-Location $env:SANDBOX_APP_DIR; git diff --stat {quote('origin/'+self.base+'...HEAD')}; git log --oneline {quote('origin/'+self.base+'..HEAD')}")

        def agent_command(self, prompt_path, model, timeout_min):
            computer = (" --strict-mcp-config --mcp-config " + quote(self.work + '/computer-mcp.json')) if self.project.get('computer_use') else ''
            return (f"Set-Location $env:SANDBOX_APP_DIR; [IO.File]::ReadAllText({quote(prompt_path)}) | "
                    f"& claude -p --model {quote(model)} --dangerously-skip-permissions --output-format stream-json --verbose" + computer + "; exit $LASTEXITCODE")

        def save_agent_changes(self):
            return self.sb("Set-Location $env:SANDBOX_APP_DIR; git add -u; if($LASTEXITCODE){exit $LASTEXITCODE}; "
                           "git diff --cached --quiet; if($LASTEXITCODE -eq 1){git commit -q -m 'sandbox: uncommitted changes by agent'}; "
                           "git status --porcelain | Select-String '^\\?\\?' | Select-Object -First 20", check=False).strip()

        def output_exists(self, name):
            return bool(self.sb(f"$p={quote(self.work+'/'+name)}; if((Test-Path -LiteralPath $p -PathType Leaf) -and (Get-Item -LiteralPath $p).Length -gt 0) {{ 'ok' }}").strip())

        def has_changes(self):
            return bool(self.sb(f"Set-Location $env:SANDBOX_APP_DIR; git log --oneline {quote('origin/'+self.base+'..HEAD')}").strip())

        def preserve(self):
            if self.dry: return ''
            patch = self.sb(f"Set-Location $env:SANDBOX_APP_DIR; git diff --binary {quote('origin/'+self.base)}", check=False)
            if patch: (self.run_dir / 'wip.patch').write_text(patch, encoding='utf-8')
            return ''

        def run_code(self, step):
            if self.dry: return True, '(dry-run)'
            name = step['code']
            log_path = self.run_dir / f"code-{step['id']}-{len(self.state['history'])}.log"
            self.set_current(step['id'], 'code', log_path.name)
            if name == 'gates.sh':
                remote = self.work + '/gates.ps1'
                self.scp_to(self.project_dir / self.project['gates'], remote)
                rc, out = self.run_remote(f"Set-Location $env:SANDBOX_APP_DIR; $env:BASE={quote(self.base)}; & {quote(remote)}; exit $LASTEXITCODE", log_path)
                self.write_remote(self.work + '/gates.txt', out)
                return rc == 0 and not re.search(r'^FAIL(?:\s|$)', out, re.M), out[-4000:]
            if name != 'pr-create.sh': return False, f'unsupported Windows code step: {name}'
            self.refresh_token()
            if not self.has_changes(): return False, 'no commits to publish'
            parts = ['aifactoryのWindows VMで実装・検証した変更です。', '']
            for file in ('report.md', 'review.md', 'gates.txt'):
                value = self.vm_read(file).strip()
                if value: parts += ['## '+file, '', value, '']
            self.write_remote(self.work+'/pr-body.md', '\n'.join(parts))
            rc, out = self.run_remote(f"Set-Location $env:SANDBOX_APP_DIR; git push -q -u origin {quote(self.branch)}; if($LASTEXITCODE){{exit $LASTEXITCODE}}; "
                                     f"gh pr create --base {quote(self.base)} --head {quote(self.branch)} --title {quote(self.title)} --body-file {quote(self.work+'/pr-body.md')}; exit $LASTEXITCODE", log_path)
            urls = re.findall(r'https://github\.com/' + re.escape(self.project['repo']) + r'/pull/\d+', out)
            if rc or not urls: return False, out[-4000:]
            self.write_remote(self.work+'/pr_url', urls[-1]+'\n')
            return True, urls[-1]

        def release(self):
            if self.dry: return
            script = f"$root={quote(self.work)}; " + r'''
$out=@{}; $total=0
foreach($f in Get-ChildItem -LiteralPath $root -Force) {
 if($f.Name -eq 'runtime.env'){continue}
 if($f.PSIsContainer -or ($f.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'non-regular artifact'}
 $total += $f.Length
 if($total -gt 4194304){throw 'artifact limit exceeded'}
 $b=[IO.File]::ReadAllBytes($f.FullName)
 $hash=[Security.Cryptography.SHA256]::Create()
 try {$digest=([BitConverter]::ToString($hash.ComputeHash($b))).Replace('-','').ToLowerInvariant()} finally {$hash.Dispose()}
 $out[$f.Name]=@{data=[Convert]::ToBase64String($b); sha256=$digest}
}
ConvertTo-Json -InputObject $out -Compress -Depth 4
'''
            self.accept_artifacts(json.loads(self.sb(script)))
    return WindowsRun
