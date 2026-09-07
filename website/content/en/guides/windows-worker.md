# Windows worker setup and operations

For initial setup, see [Install with one command](worker-install.md).

Run aifactory tickets as an ordinary user inside a dedicated Windows VM. A Go Windows service pulls operations over HTTPS, executes PowerShell commands, and returns logs and artifacts. Use the existing MCP `ticket_run` or `kb run` interface.

## Execution and isolation

Unlike the Mac backend, which clones and deletes a Tart VM for each run, the Windows backend keeps its VM running. Release removes the run workspace and temporary profile; **it does not reset the entire operating system**. Use a dedicated VM for trusted projects under one administrator. GUI automation and VM snapshot rollback are outside this version's scope.

The service runs as LocalSystem. Commands run under a separate ordinary local account. ACLs prevent that account from reading service credentials, its own logon password, or the operation journal. Project GitHub App and Claude credentials arrive through private transient stdin and remain outside operation descriptions and collected artifacts.

## Prepare the VM and network

Use Windows 11 x64, Windows PowerShell 5.1, Git for Windows, GitHub CLI, and Claude CLI. Install tools on the machine PATH. The default Git Bash path is `C:\Program Files\Git\bin\bash.exe`. Go is needed only on the build machine.

Apply network restrictions at Proxmox: deny new inbound connections and access to private networks, link-local addresses, tailnet, and unused IPv6. Allow only the worker HTTPS endpoint, public DNS, and required public HTTPS traffic. Do not expose the console or Proxmox management API to tasks. Verify allowed and blocked destinations from the guest; the worker's `network_ready` field does not inspect the external firewall.

A source-restricted TCP relay can forward to a control plane on another Proxmox host. Keep TLS intact through the relay and match the URL hostname to the certificate SAN. Use a clean dedicated image without personal accounts, autologon, or credentials.

## Install the service

Follow the [pull control-plane instructions](https://github.com/akkijp-oss/aifactory/blob/main/workers/README.md) to enroll a Windows worker ID. Transfer its token and trusted CA through an authenticated administration channel, such as SSH/QGA. Never put secrets in an installer download directory.

```bash
cd workers
CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -trimpath -o /tmp/aifactory-worker.exe ./cmd/aifactory-worker
```

Before transferring credentials, create `C:\ProgramData\AIFactoryWorker\private` with access limited to SYSTEM and Administrators. Place `worker.token` and `server.crt` there. Prepare this configuration:

```json
{
  "worker": "windows-worker-01",
  "url": "https://ctl.example.internal:8766",
  "token_file": "C:\\ProgramData\\AIFactoryWorker\\private\\worker.token",
  "ca_file": "C:\\ProgramData\\AIFactoryWorker\\private\\server.crt",
  "state_dir": "C:\\ProgramData\\AIFactoryWorker\\private\\state",
  "work_root": "C:\\ProgramData\\AIFactoryWorker\\work",
  "task_user": "aifactory-task",
  "task_password_file": "C:\\ProgramData\\AIFactoryWorker\\private\\task.password"
}
```

Run the [installer](https://github.com/akkijp-oss/aifactory/blob/main/workers/templates/install-windows-worker.ps1) in Administrator PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-windows-worker.ps1 -WorkerExe C:\Setup\aifactory-worker.exe -ConfigFile C:\Setup\config.json
Get-Service AIFactoryWorker
```

It creates a separate task account with a random protected password, grants batch logon, applies ACLs, and registers an automatic service with restart recovery. It refuses to overwrite an existing account or service. For binary updates, stop the service, replace the executable, and start it again. Preserve the journal.

## Register a project

```yaml
name: example-windows
repo: organization/repository
base_branch: main
backend: windows-pull
worker: windows-worker-01
app_dir: C:/ProgramData/AIFactoryWorker/work/app
gates: gates.ps1
```

`app_dir` must be `<work_root>/app`; the runner translates it to `<work_root>/<lease>/app`. Artifacts go into `<work_root>/<lease>/work/<task>`. Configure project-scoped GitHub App and Claude credentials on the control plane.

Optional `provision.ps1` runs as the ordinary task account before credentials and cloning. Preinstall tools that require Administrator privileges. Use a `.ps1` gate and propagate external command failures explicitly with `exit $LASTEXITCODE`. The shared workflow step identifier remains `gates.sh`; the Windows backend invokes the project's PowerShell gate. PR creation uses native Git and gh. Automatic PR merge is unsupported.

Create and run a ticket through MCP or `kb run <id>`. Dispatch skips offline or occupied workers. Each worker owns one run lease at a time.

## Cancellation, artifacts, and recovery

A Windows Job Object contains the command and descendants. Completion, cancellation, and timeout terminate descendants too. Closing the job handle after a worker crash also terminates them. Failure to confirm termination leaves the operation `uncertain`. A journaled operation is never automatically executed again after restart.

Artifacts must be direct regular files, with a combined limit of 4 MiB. Inputs are limited to 350 KB and operation logs to 16 MiB. The runner verifies SHA-256 before releasing the workspace. It excludes `runtime.env`. Reparse points, including junctions, cause cleanup to stop and retain the lease.

`--keep` retains the workspace and lease after collection. `--resume` requires the same owned lease and completed repository/ticket setup. Incomplete initial setup is not automatically recreated. Inspect processes and state before using `control show` and `resolve --confirmed-stopped`. Lease release requires a successful `guest-release` operation. The Windows VM continues running.

Use `Get-Service AIFactoryWorker` for service status and control-plane operation/job/run records for logs and results. Never delete the journal to retry an operation that might already have started.

## Verified scope

On 2026-09-07, a dedicated Proxmox Windows 11 Pro x64 VM passed automatic service recovery after a VM reboot without user login, service heartbeat, ordinary-user execution, Unicode output, exit-code propagation, service-token access denial, descendant termination after completion/timeout/queue cancellation, journal recovery, and TLS tests. A research ticket ran through MCP, produced research and summary artifacts, and completed checksum verification and workspace/lease release. A PowerShell progress-output issue was fixed and collection resumed using the retained lease. Native PowerShell gates preserved nonzero exits.

Product builds, Windows PR publishing, GUI work, and full VM reset were not covered by this acceptance run. Service startup and communication errors are also written to the Windows Application event log under `AIFactoryWorker`.

See [Mac and Windows computer use](computer-use.md) to add desktop interaction.
