# Install a worker with one command

Run a public installer on a dedicated Apple Silicon Mac host, Windows machine, or Linux instance. Pass the control-plane endpoint, enrolled worker ID, and its token through environment variables. Proxmox is not required; configure the instance's network restrictions beforehand.

The distribution branch is `install/worker-bootstrap`. The installer downloads source from that branch and builds with an official Go toolchain verified against its SHA-256 manifest. No preinstalled Go or manually copied worker binaries are required. Initial downloads, especially a Mac VM image, may take time.

## Prepare the control plane

Run the control plane from this branch, including `POST /v1/check`, and enroll each worker separately:

```bash
python3 workers/bin/control --db "$AIFACTORY_WORKSPACE/workers/queue.sqlite3" enroll linux-worker-01 --token-file "$HOME/.config/aifactory-workers/linux-worker.token"
```

Use the worker HTTPS receiver URL, not the console URL. Transfer the generated token securely to the installation machine. Project definitions, GitHub App credentials, and Claude authentication remain on the control plane; do not pass them to this installer.

Replace `XXXXXX` below with the dedicated worker token. To avoid putting a token in shell history, obtain it privately in advance and reference the existing environment variable instead of typing a literal value.

## Linux

Run on an apt-based dedicated amd64/arm64 instance with systemd 254 or newer, such as Ubuntu 24.04 or Debian 13:

```bash
curl -fsSL https://raw.githubusercontent.com/akkijp-oss/aifactory/refs/heads/install/worker-bootstrap/workers/install.sh | sudo env AIFACTORY_URL='https://ctl.example.com:8766' AIFACTORY_WORKER='linux-worker-01' AIFACTORY_TOKEN='XXXXXX' bash
```

Installs an ordinary task account, Git, gh, Claude CLI, Xvfb/Openbox, gmrun, Japanese fonts, Mousepad, and systemd worker/desktop services. Press `Alt+F2` to launch an application. Services start after reboot. Native Wayland is unsupported.

## Mac

Run as the logged-in user on an Apple Silicon Mac with Homebrew and Apple Command Line Tools installed. Do not run the whole installer with `sudo bash`.

```bash
curl -fsSL https://raw.githubusercontent.com/akkijp-oss/aifactory/refs/heads/install/worker-bootstrap/workers/install.sh | AIFACTORY_URL='https://ctl.example.com:8766' AIFACTORY_WORKER='mac-worker-01' AIFACTORY_TOKEN='XXXXXX' bash
```

Prepares Tart/Softnet, a dedicated base VM, guest CLI tools and desktop helpers, and a host LaunchAgent. Softnet setup may prompt for sudo authentication. The default new image is `ghcr.io/cirruslabs/macos-sequoia-base:latest`; `AIFACTORY_MAC_BASE` selects an existing dedicated base.

**Screen Recording and Accessibility require initial approval in macOS UI.** The installer checks these permissions before reporting success. If approval is required, open the named base VM in Tart, grant access to the responsible application (Tart Guest Agent / desktop-native), stop the VM, and rerun the command. The installer does not modify the TCC database. The host LaunchAgent requires a logged-in user.

## Windows

Run in an elevated Administrator PowerShell on a dedicated Windows 11 x64 machine:

```powershell
$env:AIFACTORY_URL='https://ctl.example.com:8766'; $env:AIFACTORY_WORKER='windows-worker-01'; $env:AIFACTORY_TOKEN='XXXXXX'; $env:AIFACTORY_AUTOLOGIN='1'; irm https://raw.githubusercontent.com/akkijp-oss/aifactory/refs/heads/install/worker-bootstrap/workers/install.ps1 | iex
```

Installs Git, gh, Claude CLI, an ordinary task account, the Windows worker service, and the desktop logon task. `AIFACTORY_AUTOLOGIN=1` configures automatic login for the dedicated account. Its generated password is stored in a protected file and an LSA secret, never command arguments or plaintext Winlogon `DefaultPassword`. Automatic login takes effect after the next reboot; the installer does not force a reboot.

Omit `AIFACTORY_AUTOLOGIN` if automatic login is not desired. Computer use requires an unlocked, logged-in task-user desktop. Omitting the variable does not disable existing automatic login settings.

## Environment variables

| Variable | Meaning |
| --- | --- |
| `AIFACTORY_URL` | Required HTTPS receiver origin, without credentials, path, or query |
| `AIFACTORY_WORKER` | Required enrolled worker ID |
| `AIFACTORY_TOKEN` | Required dedicated worker token |
| `AIFACTORY_CA_B64` | Optional Base64-encoded PEM CA certificate; otherwise uses system trusted roots |
| `AIFACTORY_CA_FILE` | Existing local PEM CA file, mutually exclusive with `CA_B64` |
| `AIFACTORY_SERVER_IP` | Optional hosts mapping for the endpoint hostname; conflicting existing entries are rejected |
| `AIFACTORY_REF` | Source Git ref, default `install/worker-bootstrap`. To pin a version, use a commit SHA here and in the entry-script URL |
| `AIFACTORY_MAC_IMAGE` | Source for a new Mac base VM; pin a digest for reproducibility |
| `AIFACTORY_MAC_BASE` | Dedicated local Mac base name; an existing installation retains its current name |
| `AIFACTORY_MAC_GUEST` | Mac task guest name; default `aifactory-macos-guest` |
| `AIFACTORY_AUTOLOGIN` | Windows only: `1` configures automatic task-user login |
| `AIFACTORY_CHECK_ONLY` | `1` validates configuration and CA without changing services or tools; it does not test connectivity |

For a private CA, add `AIFACTORY_CA_B64='BASE64_PEM'` to the command. TLS verification remains enabled. Never place the worker token in public URLs, repository files, or project definitions.

## Rerun and verify

Rerunning for the same worker and endpoint rebuilds and updates it. An active lease or a different worker identity/endpoint is rejected. Journals, unrelated credentials, and the task-account password are retained. Resolve installation errors before retrying; do not delete leases or journals as recovery.

TLS and worker authentication are checked before replacing configuration, without polling for work or changing reservations. Verify `online: true` and an empty lease with control-plane `control list`, then test MCP `computer_open` → `computer_action` → `computer_close`. See the individual OS guides and [computer use](computer-use.md) for operation and recovery.
