# sandbox CLI

`sandbox/bin/sandbox` (copied to `~/.local/bin/sandbox` by `sandbox/bin/install.sh`). A bash script that sshes from the Mac into Proxmox and the gateway to lend VMs.

## The five contract operations plus ls

```
sandbox take <pj> <task-id> [--need=fable,other]   lend a free VM (DNS task-<id>.sb.internal, env injected). --need lists the key purposes required (default both); stops with 鍵なし: when the pool has none
sandbox ssh <task-id> [cmd...]   log in as dev / run a command (via login shell)
sandbox url <task-id>            http://task-<id>.sb.internal:3000
sandbox reset <task-id>          roll back to snapshot clean (stays lent, env re-injected)
sandbox release <task-id> [--force]  roll back and return (--force: drop from the ledger even if the rollback failed)
sandbox ls                       list the pool VMs (who it is lent to / IP / power state)
sandbox status [pj]              pool sizes (defined / actual / lent / free)
```

There is also `sandbox idle-stop [--hours N] [--dry-run]` as an operational aid (stops VMs nobody is using; see below).

| Operation | What it does | Fails when |
|---|---|---|
| `take` | Picks a free VM of the project's pool and reserves it in `state.json` (this much runs inside the `state.json.lock` critical section, so concurrent takes never pick the same VM) → `qm rollback clean` → writes the project env and a GitHub App token to `/run/sandbox/env` → registers in dnsmasq on sb-gw → confirms the reservation. A failure on the way drops the reservation | No free VM, no project env, App not installed |
| `ssh` | `ssh dev@10.77.1.N` (ProxyJump if `SB_JUMP` is set). cmd runs through a login shell (`/etc/profile.d/sandbox.sh` loads the env) | VM unreachable |
| `url` | Prints `http://task-<id>.<SB_DOMAIN>:<APP_PORT>` | |
| `reset` | `qm rollback clean` → re-inject env. Stays lent | No `clean`; the rollback failed (exits non-zero, the VM stays lent) |
| `release` | reset → remove DNS → delete from `state.json`. On rollback lock contention it waits and retries, 3 times 10 seconds apart by default (`SB_ROLLBACK_TRIES` / `SB_ROLLBACK_WAIT`), printing every failure to stderr | The rollback failed (exits non-zero and keeps the entry in `state.json`; the message tells you what to do next). `--force` deletes the entry even when the rollback failed, for a VM you fixed by hand |
| `ls` | `TASK VM VMID IP STATUS SINCE`. TASK is the task-id it is lent to (`-` when not lent); STATUS is the Proxmox power state (running / stopped), a separate axis from lending. Returning a VM does not stop it right away, so `running` rows appear even when nothing is lent. When VMs are stopped to save power, a line `[idle-stop] N 台が節電で停止中` follows the table | |
| `status` | `PJ DEFINED ACTUAL LENT FREE`. DEFINED is the configured size (`SANDBOX_POOL_PER_PJ`, default 3), ACTUAL is how many VMs really exist on Proxmox, LENT is how many the ledger hands out, FREE is `ACTUAL - LENT` (floored at 0). Pass `pj` to print only that row | |

Example `sandbox ls` output:

```
TASK     VM             VMID   IP           STATUS    SINCE
204      sb-kumitate-01 9204   10.77.1.4    running   2026-09-06T12:00:07+09:00
-        sb-kumitate-02 9205   10.77.1.5    running
```

The *defined* size (a setting) and the *actual* size (how many VMs exist on Proxmox) are different numbers. Start as many runs as the defined size while the actual size is smaller, and the extra ones fail in `take` with *no free VM*. `sandbox status` keeps the two apart:

```
PJ             DEFINED  ACTUAL  LENT  FREE
aifactory      3        2       2     0
kumitate       3        3       0     3
```

A `take` that finds nothing free prints the breakdown and the next move:

```
[error] pj=aifactory に空きなし: 定義 3 台・実体 2 台・貸出 2 台（未構築 1 台 / clean 無し 0 台）。返却を待つ（sandbox ls）か、proxmox/40-pool.sh aifactory 1 で足してください
```

*clean 無し* counts VMs skipped because they have no `clean` snapshot. `sandbox ls` cannot see that, so those VMs still count as free in the console and in `sandbox status`.

## Stopping VMs nobody is using (`idle-stop`)

A pool VM that is not lent out keeps holding CPU and memory while it runs. `sandbox idle-stop` stops the pool VMs that are **not lent out and have not been used for a while** (ADR-0033). The systemd timer `aifactory-idle-stop.timer` on the control-plane LXC calls it every 15 minutes.

```
sandbox idle-stop [--hours N] [--keep K] [--dry-run]
```

| Item | Detail |
|---|---|
| Scope | qemu VMs named `sb-<t>-<pj>-NN` that are currently running. The control-plane `ctl` / `gw` are LXCs and never match; `-base` and `-tpl-` are excluded too |
| Candidate when | The vmid is absent from the ledger (`state.json`) and the last use was `N` hours ago or more |
| Stops when | Candidates are sorted oldest first; the newest `K` are kept running and only the ones beyond them are stopped (with `K` or fewer candidates nothing stops; ADR-0035) |
| Default `N` | `--hours` > `SB_IDLE_STOP_HOURS` > `24`. `0` disables it. Put it in `pj/<pj>.env` to override per project (for a project you want always on) |
| Default `K` | `--keep` > `SB_IDLE_STOP_KEEP` > `10`. `0` stops every candidate. Global only (no per-project override) |
| Last use | `~/.config/sandbox/last-used.json`, updated by `take` / `reset` / `release` / `reinject`. With no record it falls back to the VM's `uptime`; if that is unavailable too, the VM is **left running** |
| How it stops | `status/shutdown` (120 s timeout; guest agent / ACPI), then `status/stop` if that did not take |
| Record | `~/.config/sandbox/idle-stop.json` (`hours` / `keep` / `last_run` / `stopped[]` / `candidates[]` = candidates still running). The `sandbox ls` footnote, the console and the MCP `sandbox_status` read it |
| `--dry-run` | Prints the verdicts without stopping anything and without writing the record |

Example output:

```
[idle-stop] sb-main-kumitate-02 (9205): shutdown（最終利用 51h12m 前、24h 超）
[idle-stop] sb-main-kumitate-01 (9204): 候補だが残す（最終利用 26h03m 前。新しい方から 10 台の内）
[idle-stop] 対象 13 台: 候補 11（停止 1 / 残す 10。足切り 10 台）/ 貸出中 1 / 未経過 1 / 不明 0
```

**There is no command to start a VM back up.** The next `take` does it: `rollback` starts a stopped VM and waits for ssh. That costs an extra 30–60 seconds, and this line appears in the log:

```
[start] vm 9205: 停止中だったので起動した（42 秒）
```

`state.json.lock` is held per VM from the verdict until the VM has actually stopped, so a VM that `take` has just reserved is never shut down under it. A concurrent `take` therefore waits up to `SB_LOCK_WAIT` seconds (default 150).

## Operational helpers (outside the contract)

```
sandbox keys list|add|set|rm|token          the Claude key pool (keys.json on the control plane; the only source of the Claude keys handed to VMs). Each key says whether it is used for Fable and/or for Opus, Sonnet and Haiku; take picks one per model
sandbox token set <pj|global> gh            GitHub token (fallback when there is no GitHub App). Claude keys are refused here: use sandbox keys add
sandbox token rotate gh                     replace GH_TOKEN in the global file, every project file that holds it and ctl.env from one prompt
sandbox token rotate claude[:<family>]      replace intake's key in ~/.config/aifactory/ctl.env (control plane only) and restart the console. Does not touch the pool
sandbox token show [pj]                     where keys come from: intake's key (masked), the GitHub token source, the pool's key counts, and [stale] Claude lines left in env files
sandbox token clear <pj|global> gh          remove a GitHub token
sandbox reinject <task-id>|--all            re-inject the current settings into lent VMs (no rollback; for key rotation)
sandbox gh-app status|token <pj>|refresh    GitHub App: check settings / print an installation token for <pj> / reissue GH_TOKEN to every lent VM
```

### keys (the source of Claude keys)

Named Claude keys kept in `~/.config/sandbox/keys.json` (mode 600) on the control plane, one picked per model by `take` / `reset` / `reinject` (Proxmox) and by `keys pick` (the Mac / Windows / Linux pull backends) (ADR-0044 / ADR-0045). Each key says whether it is used for Fable (`--fable`; planning, design and review steps) and/or for Opus, Sonnet and Haiku (`--other`; implementation and research steps), so contracts can be kept apart. The console's *Keys* screen edits the same file.

| Command | What it does |
|---|---|
| `keys add <name> [--fable] [--other] [--note …]` | Register a key. The value is read from the terminal without echo, or from stdin. At least one purpose is required. The name must be unique and match `[A-Za-z0-9._-]{1,40}` |
| `keys list [--json]` | Name, purposes, enabled, the last 4 characters of the token, issue date, assignment count (`ASSIGNED` = how many times take / reinject handed the key out), last launch, launch count (`LAUNCHES` = how many times the runner actually started `claude` with it; use this as the usage estimate), and the tickets using it. The value is never printed |
| `keys used <name>` | Reporting hook the runner calls whenever it starts an agent (advances the launch count and time). Not meant to be typed by hand |
| `keys set <name> [--fable=on\|off] [--other=on\|off] [--enable\|--disable] [--note …]` | Change the purposes, enabled, or the note. Disabling prints the `reinject` commands for the VMs that hold it |
| `keys token <name>` | Replace only the value (name, purposes and counters stay). Apply it to lent VMs with `reinject` |
| `keys rm <name> [--force]` | Remove a key. `--force` is required while a lent VM still holds it |
| `keys pick --pj <pj> --task <id> [--need=…] [--current=…] --json` | Selection hook the runner calls once per step; returns the chosen key names and the per-family variables as JSON. Not meant to be typed by hand |

The pick is "the key this ticket used before (while it is still eligible), otherwise the enabled key with a matching purpose that has gone longest without being used". The pool is the only source: a `CLAUDE_CODE_OAUTH_TOKEN` in `env`, `pj/<pj>.env` or the process environment is never handed to a VM, even when the pool is empty (ADR-0060). If a purpose the run needs (the runner passes `--need=fable,other`) has no key, `take` stops with `鍵なし:` without taking a VM and the run pauses until a key is registered (ADR-0046). Keys reach the VM through the per-family variables (`CLAUDE_CODE_OAUTH_TOKEN_FABLE` / `_OPUS` / `_SONNET` / `_HAIKU`); only the name is recorded, in `CLAUDE_KEY_NAME_<FAMILY>` and in the lease ledger, so the run log reads `key=CLAUDE_CODE_OAUTH_TOKEN_OPUS (pool: opus-a)`. The runner uses only the family variable; if the VM lacks it (a run started with `--from` on a step of another model, say), the step stops with `failure: key` before `claude` is launched.

The pull backends (Mac / Windows / Linux) have no Proxmox lease ledger, so the runner calls `keys pick` once per step and passes the previously chosen names through `--current`. A run keeps the same key across its steps, and disabling that key moves the next step to another one. The chosen names are kept in the run's `state.json` under `keys`. `ASSIGNED` therefore grows once per step, and `IN_USE` never lists pull-backend runs because the ledger is Proxmox-only.

### token

| Command | Writes to |
|---|---|
| `token set <pj\|global> gh` | `GH_TOKEN` in `~/.config/sandbox/pj/<pj>.env` or `~/.config/sandbox/env` (fallback when the App is not configured) |
| `token set … claude` / `token set … claude:<family>` | **Refused** (exit non-zero, nothing written; ADR-0060). Claude keys go into the pool with `keys add` or the *Keys* screen; intake's key with `token rotate claude` |
| `token clear <pj\|global> gh` | Remove `GH_TOKEN` from that file |
| `token rotate gh` | `GH_TOKEN` in `~/.config/sandbox/env`, every `pj/*.env` that holds it, and `~/.config/aifactory/ctl.env`; restarts the console and finishes with `reinject --all` when VMs are lent out (ADR-0029) |
| `token rotate claude[:<family>]` | Only `CLAUDE_CODE_OAUTH_TOKEN` (`_<FAMILY>`) in `~/.config/aifactory/ctl.env`, the key `intake` uses for its one `claude -p` call on the control plane; restarts the console. Touches neither env / pj files nor lent VMs, and refuses on a host without `ctl.env` |
| `token show [pj]` | The host kind, one line saying VM keys come from the pool only (env-file keys are not used), intake's key from `ctl.env` (masked, days since issued), the `GH_TOKEN` source, the pool's key counts per purpose (`pool: 0 本` when `keys.json` is absent, with a note that runs pause), and a `[stale]` line for every `CLAUDE_CODE_OAUTH_TOKEN*=` left in `env` / `pj/<pj>.env`, with the `sed -i '/^CLAUDE_CODE_OAUTH_TOKEN/d' <file>` that removes it. Values are never printed |

When a key expires: a pool key is replaced with `sandbox keys token <name>` (or the *Keys* screen) and pushed into lent VMs with `reinject`; intake's key with `sandbox token rotate claude` on the control plane (the host that has `ctl.env`; it prints the `systemctl restart` command instead if `sudo -n` does not work). A `claude` process already running inside a VM still has to be restarted there.

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
| `~/.config/sandbox/env` | `SB_TENANT` (default `main`; derives `SB_PREFIX` = `sb-<t>`, `SB_DOMAIN` = `<t>.sb.internal`, `SB_POOL` = `sb-<t>`) / `PVE_HOST` (ssh alias of the Proxmox host; required in ssh mode, no default) or `PVE_API_URL` + `PVE_API_TOKEN` (API mode: a token scoped to the tenant's pool; `PVE_API_CA` or `PVE_API_INSECURE=1`; ADR-0017) / `GW_SSH` (ssh target of the gateway LXC; required, no default) / `SB_KEY` / `SB_DOMAIN` / `APP_PORT` / `SB_JUMP`  / `SB_POOL_NET` (default `10.77.1`) / `SB_POOL_BASE` (default `9200`) / `SB_IDLE_STOP_HOURS` (hours of disuse before a VM becomes a stop candidate; default 24, `0` disables) / `SB_IDLE_STOP_KEEP` (candidates kept running; default 10). Skeleton `sandbox/templates/env.example` |
| `~/.config/sandbox/tenants/<t>.env` | Another tenant's settings on the maintainer's machine. `SB_TENANT=<t>` makes `sandbox` and `proxmox/run.sh` read it; state goes to `<t>.state.json`, per-project files to `<t>.pj/` |
| `~/.config/sandbox/pj/<pj>.env` | `GH_REPO=owner/name`, optionally a fallback `GH_TOKEN`, `SB_IDLE_STOP_HOURS` (override for this project only), `APP_PORT`. No Claude keys: a `CLAUDE_CODE_OAUTH_TOKEN*` line here is ignored and reported as `[stale]` by `token show` (ADR-0060) |
| `~/.config/sandbox/keys.json` | The Claude key pool (mode 600). Edited only through `sandbox keys` and the console's *Keys* screen |
| `~/.config/sandbox/gh-app/app.env` + `private-key.pem` | GitHub App. Created by `sandbox/bin/gh-app-setup` |
| `~/.config/sandbox/state.json` | Lending table. `{ "<task-id>": {"vmid", "name", "ip", "pj", "since"} }` |
| `~/.ssh/conf.d/aifactory/config` | ssh settings for `gw.*.sb.internal` / `ctl.*.sb.internal` / `*.sb.internal` / `10.77.*`. Skeleton `ssh_config.example` |

Read order for `GH_TOKEN`: `env` (global default) → `pj/<pj>.env` (per-project override) → `SANDBOX_GH_TOKEN` (one-off override). A `GH_TOKEN` exported in the shell is **ignored** (it once silently overrode a project setting). Claude keys are not read from these files or the shell at all; `sandbox` drops any `CLAUDE_CODE_OAUTH_TOKEN*` it finds there and picks from the pool (ADR-0060).

## What is injected into the VM

`/run/sandbox/env` (tmpfs, owned by dev, `umask 077`):

```
TASK_ID=204
SANDBOX_PJ=kumitate
SANDBOX_HOST=task-204.sb.internal
CLAUDE_CODE_OAUTH_TOKEN=…                # the Opus / Sonnet / Haiku key, for a human using claude interactively in the VM
CLAUDE_CODE_OAUTH_TOKEN_FABLE=…          # one per model family, picked from the pool; the runner uses only these
CLAUDE_CODE_OAUTH_TOKEN_OPUS=…
CLAUDE_KEY_NAME_FABLE=max-akki           # the pool name, so logs can say which key; the value is never recorded
GH_TOKEN=ghs_…
GH_REPO=akkijp/kumitate
GH_TOKEN_EXPIRES_AT=2026-09-06T03:55:00Z
```

`/etc/profile.d/sandbox.sh` loads it at login and also sets `SANDBOX_APP_DIR` and PATH.

## Proxmox-side scripts {#proxmox}

`sandbox/proxmox/run.sh <script> [args]` is the entry point: it sshes into the Proxmox host named by `PVE_HOST` (required) and streams the script to it, prefixed with `_tenant.sh` (the derivation of tenant names and numbers). The Mac's public key, plus the control-plane LXC's key when one exists, is passed as `SB_PUBKEY`. `SB_TENANT=<t>` makes it read `~/.config/sandbox/tenants/<t>.env` and work on that tenant (ADR-0017).

Design values can be passed as environment variables; unset means the `main` tenant.

| Variable | Meaning | Default |
|---|---|---|
| `SB_TENANT` | Tenant slug (`[a-z0-9]{1,6}`); derives names, zone / vnet, firewall groups, pool and DNS domain (rule: every name carries `sb` and `<t>`) | `main` |
| `SB_NET` | /16 prefix of the sandbox network (different per tenant) | `10.77` |
| `SB_VMID_BASE` | First VMID of the tenant's block (gw = +0, ctl = +1, base = +100, project templates = +110…, pool = +200…; different per tenant) | `9000` |
| `SB_NODE` | Proxmox node that hosts the SDN zone | The host's hostname |
| `SB_GW_CT` / `SB_CTL_CT` / `SB_BASE_VMID` / `SB_TPL_BASE` / `SB_POOL_BASE` | Individual ids inside the block (normally derived) | `9000` / `9001` / `9100` / `9110` / `9200` |
| `SB_ZONE` / `SB_VNET` / `SB_PREFIX` / `SB_FW_GROUP` / `SB_POOL` / `SB_DOMAIN` | Overrides for the derived names | `sbmain` / `vnmain` / `sb-main` / `sb-main` / `sb-main` / `main.sb.internal` |

| Script | Creates |
|---|---|
| `05-tenant.sh [create\|token\|adopt\|show]` | Resource pool `sb-<t>`, role `AifactorySandbox`, user `sb-<t>@pve`, an ACL limited to that pool. `adopt` puts existing VMs / CTs into the pool; `token` prints an API token for use from your own machine |
| `10-sdn.sh` | SDN zone `sb<t>` / vnet `vn<t>` (`SB_NET.0.0/16`, SNAT) |
| `20-gateway-lxc.sh` | `sb-<t>-gw` LXC (dnsmasq, tailscaled, a systemd unit dropping VM-originated forwards) |
| `25-control-lxc.sh` | Control-plane LXC `sb-<t>-ctl` (checkout, workspace, `sandbox` CLI in API mode, console with `/docs/`, gh-refresh timer, the runner's tools; all under systemd). Issues the API token and writes it inside. Env: `AIFACTORY_REPO_URL` `AIFACTORY_REF` |
| `30-base-template.sh create` | `sb-base` 9100 (cloud image + cloud-init → `31-provision-base.sh` → template) |
| `31-provision-base.sh` | The base layer's contents (runs inside the VM) |
| `32-pj-template.sh` | `sb-tpl-<pj>` 911x (clone from base → the project's `provision.sh` (`workspace/projects/<pj>/` → `examples/projects/<pj>/`) → template). Env: `GH_TOKEN` `TPL_VMID` `PJ` |
| `40-pool.sh <pj> <n>` | Pool 92xx (linked clone × n → public keys (maintainer + control plane) via cloud-init → start → `clean` snapshot). Env: `TPL_VMID` |
| `45-pool-keys.sh [pj]` | Add public keys to existing pool VMs afterwards (append to `authorized_keys` through the guest agent → retake `clean`). For a control plane added after the pool. Env: `LENT` |
| `50-firewall.sh` | Datacenter firewall + groups `sb-<t>` (VMs) and `sb-<t>-ctl` (control plane; only this tenant's sections of `cluster.fw` are rewritten) + firewall=1 on every VM in the pool + retake `clean` + FORWARD DROP on sb-gw. Env: `LENT` (VMIDs to skip) |

## gh-app-setup

`sandbox/bin/gh-app-setup [app-name]`. Creates a GitHub App through the manifest flow and saves the App id and private key to `~/.config/sandbox/gh-app/`. Default name `aifactory-sandbox` (must be unique across GitHub). Default permissions: contents / pull_requests write, metadata / actions / checks read.

## launchd

`sandbox/templates/launchd/com.aifactory.sandbox.gh-refresh.plist`. Runs `sandbox gh-app refresh` every 45 minutes. Copy to `~/Library/LaunchAgents/` and `launchctl load`. It calls the copied `~/.local/bin/sandbox` (a symlink would fail under TCC).
