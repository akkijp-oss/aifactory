# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- Console ticket: the run panel now matches what the ticket can actually do. A done ticket used to show an enabled green *Run* next to a note saying to reopen it first, and `kb run` then refused it; *Run* is now disabled there (with the reason as its title) and *Back to todo (redo)* sits beside it, while dry run stays available. The note under the button is written per state (todo, in progress, review, waiting for a human, done), and the empty run-record text no longer points at a button that is not on screen: it tells you to add `project.yml` when the project has none, to wait when a job is running, and to reopen the ticket when it is done
- Console: a finished `kb run` job no longer pushes recovery steps for a ticket that has moved on since. `GET /api/jobs/<id>` now carries the ticket's current state and whether it was updated after the job, so the job's *What to do next* panel turns past tense (primary button *Open the ticket*) once the ticket is done or was edited later, and always shows the job's end time next to the ticket's current state. *Sync the state from the run record* now previews itself first: the new `kb sync --dry-run` (and `GET /api/tickets/<id>/sync-preview`) reports the state and note before and after without writing, the dialog shows both and warns in danger colours when the ticket was updated after that run, and the toast offers *Undo*
- Console board: the pipeline strip counted every project while the columns below it counted only the selected one, so a project filter looked broken. The strip, the "in progress" run list and the columns now all come from the filtered ticket list, the scope is stated above the strip, and the columns follow the strip's order (todo, in progress, review, done, then "waiting for a human" to the side)
- Console intake: typing a request and then stepping over to the logs or settings threw the draft away, with no warning before leaving. All nine fields of both panels (request text, project, kind, dry run, title, body, PR number) are now kept in `sessionStorage` as you type, so a detour, a reload and the browser's back button all bring them back; a *Discard the draft* button (with *Undo* in the toast) is the only way to clear them, and a successful submit clears only that panel
- Console: a run directory that only has `ticket.md` (the runner stopped before writing `state.json`) no longer shows up as "in progress, step 0" with every field empty. `run_summary` now reports `status` (`not_started` / `running` / `finished`), `overview` splits it out as `runs_not_started` and keeps `runs_active` to genuinely running runs, and the board, the run list and the run page label those records 開始前（記録なし）with their name, say which count belongs to tickets and which to run records, and still link to the detail page
- 種別・workflow・役割の一覧から `.` / `_` 始まりのファイル（macOS の `._bug.yml` など）を除いた。起票の種別の初期値を `bug` にし、種別の用途を画面に出す。台帳に workflow の無い種別が入っているときは注意を出す（console / kb / intake）

## [0.3.0] - 2026-09-08

### Added
- **Mac, Windows and Linux workers (ADR-0018, 0020, 0022, 0024)**. Tickets can now run outside the Proxmox sandbox on dedicated hosts that pull work over HTTPS. A Go worker (`workers/cmd/aifactory-worker`) fetches operations from the control plane's queue with a per-worker token and an exclusive per-run lease, streams logs and artifacts back, and needs no inbound connection. macOS: the worker runs on an Apple Silicon host and clones a fresh Tart guest per run. Windows: a service inside a dedicated VM runs tasks as an ordinary user under a Job Object and hands back the workspace. Linux: a systemd service on a dedicated instance runs commands as a dedicated user in transient units. `project.yml` selects them with `backend: macos-pull | windows-pull | linux-pull` and `worker: <id>`; `workers/bin/control` enrolls workers, inspects the journal and resolves stuck operations. Guides: [Mac](docs/macos-worker.md), [Windows](docs/windows-worker.md), [Linux](docs/linux-worker.md)
- **Computer use on those workers (ADR-0023)**. `computer_use: true` in `project.yml` gives every agent in a run a `computer` MCP tool (screenshot, click, move, type including Japanese, keys, scroll) served by `workers/cmd/aifactory-computer` inside the guest; the last screenshot is collected as an artifact. The aifactory MCP server gains `computer_open` / `computer_action` / `computer_close` for driving a worker's screen directly. [Guide](docs/computer-use.md)
- **One-command installers** (`workers/install.sh` for Linux and Mac, `workers/install.ps1` for Windows). They take `AIFACTORY_URL` / `AIFACTORY_WORKER` / `AIFACTORY_TOKEN` from the environment, fetch the source at `AIFACTORY_REF` (default `main`), build with a SHA-256-verified official Go toolchain, install dependencies, the desktop (Xvfb/Openbox on Linux, auto-logon user on Windows when `AIFACTORY_AUTOLOGIN=1`), the services and the computer-use helper, then verify TLS and the token with `POST /v1/check`. Re-running on the same worker is allowed; overwriting a different worker's install or updating during a lease is refused. Verified on all three OSes from the public URL. [Guide](docs/worker-install.md)
- **Console UX pass (ADR-0019)**. Friction now scales with risk: state changes apply immediately with an *Undo* in the toast (`kb set --status <previous>`), real runs and dispatch open an in-page dialog that shows what will happen (dispatch previews the ticket `kb next` will pick, via the new `GET /api/next`), VM release and job stop use a danger-styled dialog that focuses *Cancel* and, when a run is active on that VM, requires typing the ticket number. Finished jobs show a *What to do next* panel (intake → open the created ticket, stopped run → sync the ticket state, release → sandbox). Navigation is ordered by frequency (board / file / runs / jobs / sandbox / logs / settings), dispatch moved from the intake page to a dialog on the board, and `g` + a letter jumps between screens (`?` lists them). An offline banner replaces the bare "disconnected" label
- All UI strings live in `console/static/strings.js`; `console/tests/test_strings.py` (stdlib only, runs in CI) rejects forbidden spellings, non-verb button labels, non-polite sentences, developer vocabulary, glossary drift and any key that is undefined or unused. `console/UX.md` is the one-page voice & tone guide, glossary and per-feature design table; `docs/ui-ux-writing-guide.md` holds the general know-how it applies. Server-side error messages (`core.py`, `bin/console`, `bin/mcp`) now say what happened and what to do
- **Tenants and a control plane on Proxmox (ADR-0017)**. `SB_TENANT` selects an independent environment per organization: its own SDN zone `sb<t>` / vnet `vn<t>` / `/16`, VMID block, names (`sb-<t>-…`), DNS domain `<t>.sb.internal`, firewall groups `sb-<t>`, Proxmox resource pool and user `sb-<t>`. The first environment is the `main` tenant; `07-migrate-naming.sh` moves a pre-rule setup (`sb` / `sbnet` / `aifactory` / `sb.internal`) onto the rule without stopping VMs. `sandbox/proxmox/_tenant.sh` derives all of it; `05-tenant.sh` creates the pool / role / user / ACL; `25-control-lxc.sh` builds a control-plane LXC (`<prefix>-ctl`) holding the checkout, workspace, `sandbox` CLI, web console, documentation site, GitHub App refresh timer and the runner's tools, all under systemd, and issues a pool-scoped Proxmox API token straight into it. Every name carries `sb` and the tenant slug; nothing keeps the old bare names
- `sandbox` CLI: Proxmox **API mode** (`PVE_API_TOKEN` + `PVE_API_URL` + `SB_POOL`) as an alternative to ssh + `qm`; the control plane never holds the host's root. `SB_TENANT` / `SANDBOX_ENV_FILE` pick another tenant's config from the maintainer's machine
- Console: `CONSOLE_TOKEN` passphrase (cookie via `/?token=`, or `Authorization: Bearer`) required to bind anywhere but 127.0.0.1; `/docs/` serves the built documentation site; `console/bin/install.sh --systemd`. `sandbox/bin/install.sh --systemd` installs the token refresh timer on Linux
- Website guide *Per-organization environments (tenants)* (ja / en)
- `.mcp.json` now has `aifactory-local` (was `aifactory`) and `aifactory-ctl`: `console/bin/mcp-remote` runs the MCP server inside the control-plane LXC over ssh (stdio), so AI sessions on a laptop (Claude Code, Codex CLI, any stdio MCP client) work against the Proxmox-hosted workspace. Target via `~/.config/aifactory/mcp-remote.env`
- `sandbox/proxmox/45-pool-keys.sh`: push public keys into existing pool VMs through the guest agent and retake `clean`; `40-pool.sh` now sets cloud-init `sshkeys` on every clone. `run.sh` has `AIFACTORY_LOCAL_TREE=1` to build the control plane from the local working tree

### Changed
- Console: binding to an internal (non-global) address such as `10.77.0.3`, `192.168.x`, or a tailnet `100.64/10` address no longer requires `CONSOLE_TOKEN` (ADR-0021); the tailnet and firewall are the boundary. The passphrase is still enforced when set, and still required for `0.0.0.0` / `::` and global addresses. `install.sh --systemd` applies the same rule
- `sandbox/proxmox/50-firewall.sh` rewrites only its own tenant's sections of `cluster.fw` and adds a `-ctl` group for the control plane; VMs are selected by pool membership
- `glue/bin/dispatch` counts lent VMs from the sandbox state file instead of parsing VM names (names now differ per tenant)
- `workflow/bin/run` honors `SANDBOX_STATE`, `SB_KEY` and `SB_JUMP` for its scp calls

### Fixed
- `31-provision-base.sh` creates the `dev` database (the psql check at the end failed on a fresh base template); `40-pool.sh` no longer aborts on the first pool of a project under `pipefail`; `20-gateway-lxc.sh` starts the CT before appending keys (a freshly created gateway stayed stopped)

## [0.2.0] - 2026-09-06

### Added
- `~/.config/aifactory/workspace` (one line, a path) as a second way to point at the workspace; `AIFACTORY_WORKSPACE` still wins. Works for processes that do not go through a shell. `paths.describe()` reports `workspace_source`
- Getting-started guide: how to reproduce the setup on another machine (two clones, secrets entered by hand)
- Leak guards: `.githooks/` (pre-commit on staged changes, pre-push with gitleaks) installed by `bin/install-hooks.sh`; `bin/oss-check.sh --staged`; a `secrets` CI job (gitleaks over full history); GitHub secret scanning and push protection enabled
- `examples/projects/aifactory/`: aifactory as its own sandbox project (public, tokenless clone, `mkdocs serve` on :3000 as the app, gates = CI set + `bin/oss-check.sh`). Anyone can run a ticket end to end with it; the maintainers use it for dogfooding
- `console/bin/install.sh --launchd` bakes `AIFACTORY_WORKSPACE` into the plist, so a workspace outside the repository also works for the resident console

### Fixed
- Runner: when the wip-branch push fails (for example the GitHub App lacks a permission), the diff is saved to `wip.patch` in the run directory instead of being lost with the VM rollback
- GitHub App manifest now requests `workflows: write`, and `sandbox` includes it when issuing installation tokens; without it GitHub rejects pushes that touch `.github/workflows/`
- `console/bin/install.sh`: a fullwidth parenthesis right after `$ws` was parsed as part of the variable name
- CI: the `bash -n` loop used `bash -n "$f" && echo ok`, so a syntax error in any file but the last one left the step green; it now fails on the first bad file and also checks `console/bin/install.sh`. The gate list lives in `examples/projects/aifactory/gates.sh` alone — it gained the `paths` check and `provision.sh` calls it instead of keeping its own shorter list

## [0.1.0] - 2026-09-06

First tagged version. The repository history starts here; earlier internal history was not carried over because it contained private project records.

### Added
- `lib/aifactory_paths.py`: one place that decides where operational data lives (`AIFACTORY_WORKSPACE`, default `workspace/`, git-ignored). `kb`, `run`, `intake`, `dispatch`, the console and the MCP server all use it (ADR-0016)
- `examples/projects/kumitate/`: a shipped reference project definition (`project.yml` / `provision.sh` / `gates.sh`)
- `bin/migrate-workspace.sh`: one-shot migration from the old in-repo layout
- Apache-2.0 license, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, GitHub issue / PR templates, CI (`.github/workflows/ci.yml`)
- English `README.md` (Japanese in `README.ja.md`)

### Changed
- `sandbox/bin/sandbox`: `PVE_HOST` and `GW_SSH` no longer have built-in defaults; set them in `~/.config/sandbox/env`. New `SB_POOL_NET` / `SB_POOL_BASE`
- `sandbox/proxmox/*.sh`: node name, subnet prefix and VMID bases come from `SB_NODE` / `SB_NET` / `SB_GW_CT` / `SB_BASE_VMID` / `SB_POOL_BASE` (defaults unchanged)
- The kanban `run` column stores the run directory name instead of `workflow/runs/<name>` (old values are still resolved)
- `kanban.db`, `kanban/tickets/`, `workflow/runs/` and `glue/*.log` are no longer tracked in git

### Removed
- Owner-specific project definitions, tickets, run records, infrastructure inventory and the talk transcript (moved to the private workspace)

### Fixed
- `workflow/kit/routes.env` was excluded by the `*.env` ignore rule and missing from the first commit

## [0.0.0] - 2026-09-06 (internal, not tagged)
- Internal v0/v1: sandbox on Proxmox, kanban (SQLite + `kb`), workflow runner, glue (`intake` / `dispatch`), Web console, MCP server. Fifteen ADRs
