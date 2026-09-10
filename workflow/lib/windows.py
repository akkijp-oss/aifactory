"""Native PowerShell workflow in a dedicated Windows pull-worker VM."""
import base64
import fcntl
import json
import os
import pathlib
import re
import uuid
from macos import acquire_lease, backend as pull_backend, Client


def quote(value):
    if '\0' in str(value): raise ValueError('NUL in PowerShell value')
    return "'" + str(value).replace("'", "''") + "'"


def backend(Run):
    Pull = pull_backend(Run)

    class WindowsRun(Pull):
        backend_label = 'Windows workspace'
        # code step の対応表（分類の意味は macos.py の CODE_STEPS を見ること。チケット 386）。
        # ゲストは PowerShell で kit/steps/*.sh は bash 前提。sync-base は従来どおり素通り（noop）。
        # pr-automerge.sh は Git Bash 経由で理屈の上では動かせるが、実機で確かめられないまま
        # `gh pr merge`（取り消しにくい外向きの操作）の経路を増やさない判断で unsupported にしてある。
        # Windows の PJ で auto_merge を書いた run は、途中まで進んでから黙って終わるのではなく起動前に拒否される
        # macos の表は spread しない。取り込むと macos に足した step が分類ごとここへ流れ込み、
        # 「表の更新忘れ」を test_code_steps.py が拾えなくなる（run を使い切った最後の工程で落ちる）
        CODE_STEPS = {'gates.sh': 'run', 'pr-create.sh': 'run', 'sync-base': 'noop',
                      'pr-automerge.sh': 'unsupported', 'pr-merge.sh': 'unsupported'}

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
                    # runtime.env の中身は credentials() が決める（GH_TOKEN と系統別の Claude の鍵と CLAUDE_KEY_NAME_*）。
                    # 2 つ決め打ちで写していたので鍵プールの系統別の鍵が guest に届かず、全モデルが同じ 1 本で動いていた（391）。
                    # 名前は credentials() が作る固定名だけだが、環境変数として妥当な名前に限ってから入れる
                    "$credentials.PSObject.Properties | ForEach-Object { "
                    "if ($_.Name -match '^[A-Za-z_][A-Za-z0-9_]*$') { Set-Item -Path ('env:' + $_.Name) -Value $_.Value } } }; " + cmd)

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
            self.record_needed_keys()
            import aifactory_paths as paths
            # --wait で take を呼び直せるよう、lock と lease id と Client は 1 回だけ作る（チケット 373）
            if self.run_lock is None:
                self.run_lock = open(self.run_dir / 'windows.lock', 'a')
                fcntl.flock(self.run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.client is None:
                lease = self.state.get('lease') if self.resume else f'run-{self.task}-{uuid.uuid4().hex[:16]}'
                if not lease: raise RuntimeError('Windows resume has no recorded lease')
                self.lease_id = lease
                self.paths(lease)
                self.client = Client(os.environ.get('AIFACTORY_WORKER_DB') or paths.WORKSPACE / 'workers' / 'queue.sqlite3',
                                     self.project['worker'], lease, self.run_dir)
            lease = self.lease_id
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
            acquire_lease(self, worker, lease)
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

        @staticmethod
        def key_probe_command(fam):
            """どの鍵で動くかを名前だけ調べる（PowerShell 版。出す形は Run.key_probe_command と同じ）。
            guest は PowerShell 5.1 固定（workers/cmd/aifactory-worker/platform_windows.go）で `&&` / `||` を
            解釈できないため、POSIX 版をそのまま渡すと構文エラーで stdout が空になり、
            鍵プールの起動が LAUNCHES に 1 件も数えられなかった（391 / #75）"""
            return (f"if ($env:CLAUDE_CODE_OAUTH_TOKEN_{fam}) {{ if ($env:CLAUDE_KEY_NAME_{fam}) "
                    f"{{ 'CLAUDE_CODE_OAUTH_TOKEN_{fam} (pool: ' + $env:CLAUDE_KEY_NAME_{fam} + ')' }} "
                    f"else {{ 'CLAUDE_CODE_OAUTH_TOKEN_{fam}' }} }} else {{ '{Run.NO_KEY_IN_VM}' }}")

        def token_env_prefix(self, model):
            """agent の前に置く鍵の割り当て（PowerShell 版）。鍵プールが系統ごとに選んだ鍵（runtime.env の
            CLAUDE_CODE_OAUTH_TOKEN_<系統>）をそのまま使う。無印の CLAUDE_CODE_OAUTH_TOKEN に戻る経路は無い（ADR-0060。
            無ければ run_agent が probe の `none` を見て起動前に止める）。値は guest の中でだけ展開されるので runner は鍵を持たない"""
            fam = self.token_family(model)
            if not fam: return ''
            return f"$env:CLAUDE_CODE_OAUTH_TOKEN=$env:CLAUDE_CODE_OAUTH_TOKEN_{fam}; "

        def agent_command(self, prompt_path, model, timeout_min):
            computer = (" --strict-mcp-config --mcp-config " + quote(self.work + '/computer-mcp.json')) if self.project.get('computer_use') else ''
            return (f"Set-Location $env:SANDBOX_APP_DIR; {self.token_env_prefix(model)}[IO.File]::ReadAllText({quote(prompt_path)}) | "
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
            # sync-base（base 取り込み）は POSIX の sb を前提にした runner 内蔵の実装で、ここの sb は PowerShell。
            # Windows 実機で確かめられないので従来どおりの挙動のまま素通りさせる（チケット 239）
            if name == 'sync-base': return True, 'Windows: base 取り込み（sync-base）は未対応なので飛ばした'
            log_path = self.run_dir / f"code-{step['id']}-{len(self.state['history'])}.log"
            self.set_current(step['id'], 'code', log_path.name)
            if name == 'gates.sh':
                remote = self.work + '/gates.ps1'
                self.scp_to(self.project_dir / self.project['gates'], remote)
                rc, out = self.run_remote(f"Set-Location $env:SANDBOX_APP_DIR; $env:BASE={quote(self.base)}; & {quote(remote)}; exit $LASTEXITCODE", log_path)
                self.write_remote(self.work + '/gates.txt', out)
                return rc == 0 and not re.search(r'^FAIL(?:\s|$)', out, re.M), out[-4000:]
            if name != 'pr-create.sh':
                # 起動時検証（CODE_STEPS）を通り抜けた step。ここに来るのは対応表と実装がずれたときだけ
                return False, (f'unsupported Windows code step: {name}'
                               '（PJ の auto_merge を外すか、macos / linux の worker を使うこと）')
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

        def collect(self):
            # Mac 側と同じ manifest 形（files / skipped）。回収対象外は throw せずに飛ばして名前を返す（チケット 277）
            return self.sb(f"$root={quote(self.work)}; " + r'''
$out=@{}; $skipped=@(); $total=0
foreach($f in Get-ChildItem -LiteralPath $root -Force | Sort-Object Name) {
 if($f.Name -eq 'runtime.env'){continue}
 if($f.PSIsContainer){$skipped+=@{name=$f.Name;reason='directory'}; continue}
 if($f.Attributes -band [IO.FileAttributes]::ReparsePoint){$skipped+=@{name=$f.Name;reason='symlink'}; continue}
 if($total + $f.Length -gt 4194304){$skipped+=@{name=$f.Name;reason='size'}; continue}
 $total += $f.Length
 $b=[IO.File]::ReadAllBytes($f.FullName)
 $hash=[Security.Cryptography.SHA256]::Create()
 try {$digest=([BitConverter]::ToString($hash.ComputeHash($b))).Replace('-','').ToLowerInvariant()} finally {$hash.Dispose()}
 $out[$f.Name]=@{data=[Convert]::ToBase64String($b); sha256=$digest}
}
ConvertTo-Json -InputObject @{files=$out; skipped=@($skipped)} -Compress -Depth 5
''')
    return WindowsRun
