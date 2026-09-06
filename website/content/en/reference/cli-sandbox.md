# sandbox CLI

`sandbox/bin/sandbox` (copied to `~/.local/bin/sandbox` by `sandbox/bin/install.sh`). A bash script that sshes from the Mac into Proxmox and the gateway to lend VMs.

## The five contract operations plus ls

```
sandbox take <pj> <task-id>      lend a free VM (DNS task-<id>.sb.internal, env injected)
sandbox ssh <task-id> [cmd...]   log in as dev / run a command (via login shell)
sandbox url <task-id>            http://task-<id>.sb.internal:3000
sandbox reset <task-id>          roll back to snapshot clean (stays lent, env re-injected)
sandbox release <task-id>        roll back and return
sandbox ls                       lending status
```

| Operation | What it does | Fails when |
|---|---|---|
| `take` | Picks a free VM of the project's pool from `state.json` → `qm rollback clean` → writes the project env and a GitHub App token to `/run/sandbox/env` → registers in dnsmasq on sb-gw → records in `state.json` | No free VM, no project env, App not installed |
| `ssh` | `ssh dev@10.77.1.N` (ProxyJump if `SB_JUMP` is set). cmd runs through a login shell (`/etc/profile.d/sandbox.sh` loads the env) | VM unreachable |
| `url` | Prints `http://task-<id>.<SB_DOMAIN>:<APP_PORT>` | |
| `reset` | `qm rollback clean` → re-inject env. Stays lent | No `clean` |
| `release` | reset → remove DNS → delete from `state.json`. Waits and retries on rollback lock contention | |
| `ls` | `TASK VM VMID IP STATUS SINCE` | |

Example `sandbox ls` output:

```
TASK     VM             VMID   IP           STATUS    SINCE
204      sb-kumitate-01 9204   10.77.1.4    running   2026-09-06T12:00:07+09:00
-        sb-kumitate-02 9205   10.77.1.5    running
```

## Operational helpers (outside the contract)

```
sandbox token set <pj|global> [claude|gh]   enter a token interactively and save it (default claude), into the per-project file
sandbox token show [pj]                     which token is in effect (masked)
sandbox token clear <pj|global> [claude|gh] remove a token
sandbox reinject <task-id>|--all            re-inject the current settings into lent VMs (no rollback; for key rotation)
sandbox gh-app status|token <pj>|refresh    GitHub App: check settings / print an installation token for <pj> / reissue GH_TOKEN to every lent VM
```

### token

| Command | Writes to |
|---|---|
| `token set <pj>` | `CLAUDE_CODE_OAUTH_TOKEN` in `~/.config/sandbox/pj/<pj>.env` |
| `token set <pj> gh` | `GH_TOKEN` in the same file (fallback when the App is not configured) |
| `token set global` | `~/.config/sandbox/env` (default for every project) |
| `token show [pj]` | Source and masked value of the effective token |

### gh-app

| Command | What it does |
|---|---|
| `gh-app status` | App id, permission list (required: contents / pull_requests write, metadata / actions read; optional: checks read), installations, per-project token availability, the URL to change permissions |
| `gh-app token <pj>` | Issues and prints an installation token scoped to the project's repository (one hour) |
| `gh-app refresh` | Reissues `GH_TOKEN` to every lent VM. Called by launchd every 45 minutes |

The permissions requested are "those we want that the App actually holds". Add a permission on the App, approve it on each installation, and it rides along without touching the CLI.

## Configuration

| File | Contents |
|---|---|
| `~/.config/sandbox/env` | `PVE_HOST` (ssh alias of the Proxmox host; required, no default) / `GW_SSH` (ssh target of the gateway LXC; required, no default) / `SB_KEY` / `SB_DOMAIN` / `APP_PORT` / `SB_JUMP` / `SB_POOL_NET` (default `10.77.1`) / `SB_POOL_BASE` (default `9200`). Skeleton `sandbox/templates/env.example` |
| `~/.config/sandbox/pj/<pj>.env` | `GH_REPO=owner/name`, `CLAUDE_CODE_OAUTH_TOKEN`, (fallback `GH_TOKEN`) |
| `~/.config/sandbox/gh-app/app.env` + `private-key.pem` | GitHub App. Created by `sandbox/bin/gh-app-setup` |
| `~/.config/sandbox/state.json` | Lending table. `{ "<task-id>": {"vmid", "name", "ip", "pj", "since"} }` |
| `~/.ssh/conf.d/aifactory/config` | ssh settings for `sb-gw` / `*.sb.internal` / `10.77.*`. Skeleton `ssh_config.example` |

Read order: `env` (global default) → `pj/<pj>.env` (per-project override) → `SANDBOX_CLAUDE_TOKEN` / `SANDBOX_GH_TOKEN` (one-off override). A `CLAUDE_CODE_OAUTH_TOKEN` / `GH_TOKEN` exported in the shell is **ignored** (it once silently overrode a project setting).

## What is injected into the VM

`/run/sandbox/env` (tmpfs, owned by dev, `umask 077`):

```
TASK_ID=204
SANDBOX_PJ=kumitate
SANDBOX_HOST=task-204.sb.internal
CLAUDE_CODE_OAUTH_TOKEN=…
GH_TOKEN=ghs_…
GH_REPO=akkijp/kumitate
GH_TOKEN_EXPIRES_AT=2026-09-06T03:55:00Z
```

`/etc/profile.d/sandbox.sh` loads it at login and also sets `SANDBOX_APP_DIR` and PATH.

## Proxmox-side scripts {#proxmox}

`sandbox/proxmox/run.sh <script> [args]` is the entry point: it sshes into the Proxmox host named by `PVE_HOST` (required) and streams the script to it. The Mac's public key is passed as `SB_PUBKEY`. The network and VMID design values can be passed as environment variables (each script's default applies when unset).

| Variable | Meaning | Default |
|---|---|---|
| `SB_NODE` | Proxmox node that hosts the SDN zone | The host's hostname |
| `SB_NET` | /16 prefix of the sandbox network | `10.77` |
| `SB_GW_CT` | CT id of the gateway LXC | `9000` |
| `SB_BASE_VMID` | VMID of the base template | `9100` |
| `SB_POOL_BASE` | First VMID of the pool (match `SB_POOL_BASE` on the Mac side) | `9200` |

| Script | Creates |
|---|---|
| `10-sdn.sh` | SDN zone `sb` / vnet `sbnet` (10.77.0.0/16, SNAT) |
| `20-gateway-lxc.sh` | `sb-gw` LXC 9000 (dnsmasq, tailscaled, a systemd unit dropping VM-originated forwards) |
| `30-base-template.sh create` | `sb-base` 9100 (cloud image + cloud-init → `31-provision-base.sh` → template) |
| `31-provision-base.sh` | The base layer's contents (runs inside the VM) |
| `32-pj-template.sh` | `sb-tpl-<pj>` 911x (clone from base → the project's `provision.sh` (`workspace/projects/<pj>/` → `examples/projects/<pj>/`) → template). Env: `GH_TOKEN` `TPL_VMID` `PJ` |
| `40-pool.sh <pj> <n>` | Pool 92xx (linked clone × n → start → `clean` snapshot). Env: `TPL_VMID` |
| `50-firewall.sh` | Datacenter firewall + group `sandbox` + firewall=1 on every VM + retake `clean` + FORWARD DROP on sb-gw. Env: `LENT` (VMIDs to skip) |

## gh-app-setup

`sandbox/bin/gh-app-setup [app-name]`. Creates a GitHub App through the manifest flow and saves the App id and private key to `~/.config/sandbox/gh-app/`. Default name `aifactory-sandbox` (must be unique across GitHub). Default permissions: contents / pull_requests write, metadata / actions / checks read.

## launchd

`sandbox/templates/launchd/com.aifactory.sandbox.gh-refresh.plist`. Runs `sandbox gh-app refresh` every 45 minutes. Copy to `~/Library/LaunchAgents/` and `launchctl load`. It calls the copied `~/.local/bin/sandbox` (a symlink would fail under TCC).
