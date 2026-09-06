# Set up the Mac

What this page tells you: from cloning the repository to having the five Mac-side CLIs working. You can get this far even before the sandbox exists.

## 1. Clone the repository

```bash
git clone git@github.com:akkijp-oss/aifactory.git
cd aifactory
```

The runner and intake need only two Python packages.

```bash
python3 -m pip install --user pyyaml jsonschema
python3 -c "import yaml, jsonschema; print('ok')"
```

## 2. Place keys and config files 🤖

Create the key used to reach the VMs and the gateway, the CLI config, and the ssh config. All secrets live **outside the repository** (`~/.config/sandbox/` and `~/.ssh/conf.d/aifactory/`).

```bash
mkdir -p ~/.ssh/conf.d/aifactory ~/.config/sandbox
[ -f ~/.ssh/conf.d/aifactory/sb_ed25519 ] || ssh-keygen -t ed25519 -N "" -C "aifactory-sandbox" -f ~/.ssh/conf.d/aifactory/sb_ed25519
grep -q "conf.d/aifactory" ~/.ssh/config || echo "Include ~/.ssh/conf.d/aifactory/config" >> ~/.ssh/config
cp sandbox/templates/ssh_config.example ~/.ssh/conf.d/aifactory/config
cp sandbox/templates/env.example ~/.config/sandbox/env && chmod 600 ~/.config/sandbox/env
```

Adjust `~/.config/sandbox/env` to your environment. `PVE_HOST` and `GW_SSH` have no defaults; without them `sandbox` stops with an error.

| Variable | Meaning | Default |
|---|---|---|
| `PVE_HOST` | ssh alias of the Proxmox host (for example `pve1`) | none (required) |
| `GW_SSH` | ssh target for the gateway LXC (for example `root@10.77.0.2`) | none (required) |
| `SB_KEY` | Private key for VMs / LXC | `~/.ssh/conf.d/aifactory/sb_ed25519` |
| `SB_DOMAIN` | DNS domain of the VMs | `sb.internal` |
| `APP_PORT` | Application port | `3000` |
| `SB_JUMP` | Empty for direct tailnet access. While Tailscale is unavailable, set it to the same value as `PVE_HOST` to go through the Proxmox host with ProxyJump | empty |
| `SB_POOL_NET` | The /24 prefix where pool VMs live. Keep it equal to `SB_NET.1` on the Proxmox side | `10.77.1` |
| `SB_POOL_BASE` | The first VMID of the pool. Keep it equal to `SB_POOL_BASE` on the Proxmox side | `9200` |

Do not put tokens (`CLAUDE_CODE_OAUTH_TOKEN` / `GH_TOKEN`) in this file; they are saved per project in the next section.

### Where operational data lives (workspace)

Project definitions, tickets, run records and logs live outside the repository in the **workspace**. The default is `<repo>/workspace/` (git-ignored); the environment variable `AIFACTORY_WORKSPACE` moves it elsewhere.

```
$AIFACTORY_WORKSPACE/
├── projects/<pj>/     # project.yml / provision.sh / gates.sh (project definition)
├── kanban/            # kanban.db / tickets/ / BOARD.md
├── runs/<run>/        # run records
├── logs/              # intake.log / dispatch.log
└── docs/              # your own notes
```

Project definitions are looked up in `workspace/projects/<pj>/` first and then `examples/projects/<pj>/`, so the bundled example (`kumitate`) works without copying. Create your own project by copying the example ([Add a project](../guides/add-project.md)).

## 3. Put the sandbox CLI on PATH 🤖

```bash
sandbox/bin/install.sh        # copies the file to ~/.local/bin/sandbox
sandbox ls                    # prints the lending table (empty is fine) if the config loads
```

!!! note "Why a copy rather than a symlink"
    The bash started by launchd (the periodic GitHub token refresh) cannot read under `Documents/` because of macOS TCC. A symlink fails with "Operation not permitted", so `install.sh` copies the file. **After updating `sandbox/bin/sandbox` in the repository, run `install.sh` again.**

The kanban / glue / workflow CLIs are called by their repository paths (no PATH entry needed).

```bash
kanban/bin/kb list
glue/bin/intake --help
glue/bin/dispatch --help
workflow/bin/run
```

## 4. Save a Claude Code token per project 🧑

To run Claude Code inside a VM, issue and save a long-lived token per project.

```bash
claude setup-token                 # authenticate in the browser → the token is printed
sandbox token set kumitate         # paste it interactively → saved to ~/.config/sandbox/pj/kumitate.env
sandbox token show kumitate        # masked confirmation
```

The per-project file (`~/.config/sandbox/pj/<pj>.env`) also holds `GH_REPO=owner/name`, which the GitHub App uses to scope its token.

```bash
cat ~/.config/sandbox/pj/kumitate.env
# GH_REPO=akkijp/kumitate
# CLAUDE_CODE_OAUTH_TOKEN=...
```

Why per project, and why not bake in OAuth: see [Security and secrets](../concepts/security.md) and ADR-0005 / 0006.

## 5. Create and install the GitHub App 🤖 → 🧑

Permission to push, open PRs and merge from the VMs is granted through **one-hour GitHub App tokens**, not static tokens (ADR-0008).

```bash
sandbox/bin/gh-app-setup            # opens a browser → 🧑 press "Create GitHub App" → saved to ~/.config/sandbox/gh-app/
sandbox gh-app status               # 🧑 install the App on each owner via the printed install link
```

You are done when `status` shows `OK` for every project. The App needs Contents (write), Pull requests (write), Metadata (read), Actions (read) and Workflows (write; without it GitHub rejects pushes that touch `.github/workflows/`). Checks (read) is optional.

Tokens expire after an hour, so register a launchd job that refreshes them every 45 minutes.

```bash
cp sandbox/templates/launchd/com.aifactory.sandbox.gh-refresh.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.aifactory.sandbox.gh-refresh.plist
launchctl list | grep aifactory
```

## 6. Check

```bash
ls -l ~/.ssh/conf.d/aifactory/ ~/.config/sandbox/ ~/.config/sandbox/pj/
sandbox gh-app status
sandbox token show
```

If the sandbox already exists, also check connectivity.

```bash
ping -c1 -W1 10.77.0.2 && echo "tailnet → sb-gw OK"
dig +short sb-gw.sb.internal
sandbox ls
```

If sb-gw is unreachable, see Step 2 of [Build the sandbox](build-sandbox.md) or [Troubleshooting](../troubleshooting.md).

## 7. Reproduce the setup on another Mac

The rule that keeps your machine in the same shape as any other user's: two clones, and secrets are never synced.

```
~/Documents/GitHub/
├── <org>/aifactory              # the public framework (a clone of this repository)
└── <you>/aifactory-workspace    # operational data (a private repository: project definitions, tickets, run records, private notes)
```

1. Clone both. If you have no workspace yet, start with empty `projects kanban runs logs docs` directories
2. Point at the workspace: `echo ~/Documents/GitHub/<you>/aifactory-workspace > ~/.config/aifactory/workspace` (or `export AIFACTORY_WORKSPACE=…` in your shell; the variable wins when both exist)
3. In the framework clone: `pip install pyyaml jsonschema`, `bin/install-hooks.sh` (with gitleaks on PATH), `console/bin/install.sh --launchd`, `sandbox/bin/install.sh`
4. Enter secrets by hand: `~/.config/sandbox/env` (from `env.example`), `~/.config/sandbox/pj/<pj>.env` (`sandbox token set`), the GitHub App under `~/.config/sandbox/gh-app/`, the ssh key under `~/.ssh/conf.d/aifactory/`. None of these go into any repository
5. Done when `kanban/bin/kb list` and http://127.0.0.1:8765/ show the workspace contents

The workspace is its own private git repository: when tickets and run records accumulate, commit and push there too (they never show up in the framework clone's `git status`).
