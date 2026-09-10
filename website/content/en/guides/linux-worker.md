# Standalone Linux worker

For initial setup, see [Install with one command](worker-install.md).

Install the worker directly on a dedicated Linux instance: physical hardware, a VPS, or a VM on any hypervisor. Worker execution does not use Proxmox APIs, VM IDs, cloning, or snapshots.

The root `aifactory-worker.service` pulls commands from the existing HTTPS queue. Commands run as the ordinary `aifactory-task` user in transient systemd services. Each operation owns a cgroup; completion, timeout, and cancellation include service shutdown verification. A separate ordinary-user desktop agent serves authenticated requests only on `127.0.0.1:31191`.

Initial support requires systemd 254 or newer (for example Ubuntu 24.04+) and X11. Native Wayland sessions are rejected. Servers without a graphical login can use `--headless` to start a dedicated Xvfb/Openbox desktop. X11 TCP listening is disabled and access uses an authentication cookie. RDP/VNC exposure is unnecessary for computer use.

## Install

Example dependencies for Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install python3 python3-pil xvfb xauth x11-utils xdotool xclip openbox gmrun dbus
```

For tickets, also install Git, GitHub CLI, GNU timeout, and Claude CLI in `/usr/local/bin` or `/usr/bin`. `provision.sh` runs without root privileges, so install OS packages beforehand.

Build from `workers/` with Go 1.25 or newer. Use `GOARCH=arm64` for ARM64 hosts or `amd64` for x86-64:

```bash
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -o /tmp/linux-bin/aifactory-worker ./cmd/aifactory-worker
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -o /tmp/linux-bin/aifactory-computer ./cmd/aifactory-computer
```

Enroll on the control plane with a new token:

```bash
python3 workers/bin/control --db "$AIFACTORY_WORKSPACE/workers/queue.sqlite3" \
  enroll linux-worker-01 --token-file "$HOME/.config/aifactory-workers/linux-worker.token"
```

Transfer the token to `/etc/aifactory-worker/worker.token` and the control-plane CA certificate to `/etc/aifactory-worker/server.crt` on the instance. The directory must be root-owned mode 0700; files must be mode 0600. Never place the token in command arguments or tickets.

Create an administrator-owned configuration. These paths and the task account are fixed by the installer:

```json
{
  "worker": "linux-worker-01",
  "url": "https://control.example:8766",
  "token_file": "/etc/aifactory-worker/worker.token",
  "ca_file": "/etc/aifactory-worker/server.crt",
  "state_dir": "/var/lib/aifactory-worker/private/state",
  "work_root": "/var/lib/aifactory-worker/work",
  "task_user": "aifactory-task"
}
```

Transfer the binaries and the repository's `workers/templates/` and `workers/computer/`, preserving their relative layout. Run as root:

```bash
sudo bash workers/templates/install-linux-worker.sh /root/linux-worker.json /root/linux-bin --headless
sudo systemctl status aifactory-worker aifactory-desktop
```

To use an existing dedicated X11 login instead, omit `--headless`. Install `workers/computer/aifactory-desktop-linux.service` as the task user's `~/.config/systemd/user/aifactory-desktop.service`. From that X11 session:

```bash
systemctl --user import-environment DISPLAY XAUTHORITY XDG_SESSION_TYPE WAYLAND_DISPLAY
systemctl --user daemon-reload
systemctl --user enable --now aifactory-desktop.service
```

Provide a dedicated X11 login for `aifactory-task`; the installer does not configure that login. Do not run the existing-session and headless agents together. Keep the task account out of sudo, docker, lxd, and other privileged groups.

## Use

Project configuration:

```yaml
name: linux-app
repo: owner/repository
base_branch: main
backend: linux-pull
worker: linux-worker-01
app_dir: /var/lib/aifactory-worker/work/app
gates: gates.sh
computer_use: true
```

Set `app_dir` to `<worker work_root>/app`. The runner creates `<work_root>/<lease>/app` and collects artifacts from `<work_root>/<lease>/work/<ticket>`. Use the existing `ticket_run` / `kb run` interface. Omit `computer_use` for CLI-only projects.

Where keys come from: **the control-plane key pool (`~/.config/sandbox/keys.json`) is the source of truth for Claude keys**. The runner calls `sandbox keys pick` once per step and writes the per-family keys it chose into the guest's `runtime.env` (ADR-0044 / ADR-0046). Disabling a key moves the next step to another one. There is no other source: `pj/<pj>.env`, `env` and whatever is left in the runner's process are **never** used, even when the pool is empty (ADR-0060), and a run whose purpose has no key in the pool pauses with `鍵なし` until a key is registered. Jobs started from the console and MCP take their keys from `~/.config/aifactory/ctl.env`, re-read for every job.

Direct AIFactory MCP sessions use the same `computer_open`, `computer_action`, and `computer_close` tools. Pass the Linux worker name to `computer_open`. See [computer action arguments and artifacts](computer-use.md).

Screenshots are at most 1024 pixels wide; coordinates map to the physical desktop. Linux typing replaces the clipboard with UTF-8 text and sends Ctrl+V. Fields that prohibit pasting, or applications that use another paste shortcut, need different handling. Inspect a screenshot to verify the result.

GUI applications use `/home/aifactory-task` as their home, while ticket shells use the lease directory as `HOME`. They share a filesystem, but `~/file` resolves to different locations. To collect a GUI-generated file, enter the absolute artifact directory from the task prompt in the save dialog.

In headless mode, press `Alt+F2` to open `gmrun` and launch an installed application by name. Use `Alt+Tab` to switch windows and `Alt+F4` to close them. Install suitable fonts for applications displaying Japanese.

## Operational scope

The dedicated instance is the execution boundary. Worker credentials and journals remain root-only; only the separate desktop token is readable by the task user. Release removes the run workspace, but does not restore the OS, task user's home, desktop, clipboard, or applications started through the GUI. Use an unlocked session; do not add a screen locker to the dedicated headless desktop.

Networking follows the instance's own configuration. This worker does not apply Proxmox or Softnet network isolation. Configure any required egress restrictions with the instance firewall.

Unverified shutdown or an unfinished journal after restart retains the lease as `uncertain`; input is not automatically repeated. Inspect systemd, `journalctl`, and operation records before using the existing recovery procedure. Stop the desktop with `systemctl stop aifactory-desktop`, and the worker with `systemctl stop aifactory-worker`.

## Verification scope

Tested on dedicated Ubuntu 24.04 ARM64 with systemd as PID 1 in Docker, without Proxmox. AIFactory MCP captured the Xvfb/Openbox desktop, clicked, typed Japanese, sent keys, scrolled, and released the lease. The GUI application's contents exactly matched the 41-line / 1,464-character input.

Checks also covered denial of task-user access to worker credentials and journals, shell variable expansion, exit status 7, descendant termination on completion and timeout, cleanup without following symlink targets, and worker SIGKILL followed by operation shutdown, retained uncertainty, and verified recovery. Existing graphical logins, native Wayland, and product-specific builds were outside this test.

The Linux backend transport was also exercised for MCP configuration transfer, MCP-generated PNGs, Unicode artifact collection, hash verification, and release. GitHub cloning and LLM execution were substituted in this transport test; it was not a full product-ticket run.


On 2026-09-07, a dedicated Linux worker VM was deployed on Proxmox with Ubuntu 24.04 amd64, 4 vCPUs, 4 GiB RAM, and a 40 GiB disk. It is registered as `linux-worker-01`, with systemd services for the worker and Xvfb/Openbox. Proxmox provisions the instance and network; worker execution does not call the Proxmox API.

The same MCP screenshot, click, Japanese typing, key, scroll, and release checks passed on this VM, including an exact match for the 41-line / 1,464-character GUI input. Administrative SSH accepts keys from the host only, and the dedicated network restricts connections to private networks.


A real acceptance ticket successfully cloned from GitHub, ran Claude as the ordinary task user, launched Mousepad through `Alt+F2 → gmrun` using computer MCP, and entered and captured three Japanese lines. The operator independently read the GUI-saved file and verified the requested text. The same-position click timeout and missing launcher found in the initial acceptance ticket were fixed, with a repeated-click regression test added. Name resolution, automatic service startup, and control-plane reconnection were verified after a VM reboot.
