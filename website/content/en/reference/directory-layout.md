# Directory layout

The repository holds only the factory's **mechanism**. Operational data (project definitions, tickets, run records, logs) lives in the **workspace** (`AIFACTORY_WORKSPACE`, default `<repo>/workspace/`, not tracked by git).

## Repository

```
aifactory/
├── README.md  README.ja.md          # Overview and "to the AI reading this repository" (English / Japanese)
├── LICENSE                          # Apache-2.0
├── CONTRIBUTING.md  SECURITY.md  CHANGELOG.md
├── .github/workflows/
│   ├── ci.yml                       # unittest (console/tests, workflow/tests) + mkdocs build --strict
│   └── docs.yml                     # Deploys the documentation site (GitHub Pages)
├── examples/projects/<pj>/          # Bundled example project definitions (kumitate = akkijp/kumitate)
│   ├── provision.sh  project.yml  gates.sh
├── sandbox/                         # Section 1: where things run
│   ├── README.md                    # Design and contract
│   ├── BUILD.md                     # Build procedure (commands and completion criteria)
│   ├── STATUS.md                    # Progress table and real-machine check commands
│   ├── OPERATIONS.md                # Operations (lend/return/reset/template updates/failures)
│   ├── bin/
│   │   ├── sandbox                  # Mac-side CLI (bash)
│   │   ├── install.sh               # Copies it to ~/.local/bin
│   │   └── gh-app-setup             # Creates the GitHub App
│   ├── proxmox/                     # Proxmox-side scripts (numbered)
│   │   ├── run.sh                   # Entry point (sshes into PVE_HOST and streams the script)
│   │   ├── 10-sdn.sh  20-gateway-lxc.sh  30-base-template.sh  31-provision-base.sh
│   │   └── 32-pj-template.sh  40-pool.sh  50-firewall.sh
│   └── templates/                   # Factory-side skeletons only (nothing project-specific)
│       ├── README.md                # How to write provision.sh
│       ├── env.example  ssh_config.example  launchd/
│       └── base/README.md           # What the base layer contains
├── kanban/                          # Section 2: what to do when
│   ├── README.md
│   └── bin/kb                       # CLI (Python). The ledger lives in workspace/kanban/
├── workflow/                        # Section 3: how to proceed
│   ├── README.md
│   ├── kit/                         # ★Shared by every project. The only place procedures live
│   │   ├── schema/                  # workflow.schema.json / project.schema.json
│   │   ├── roles/                   # _common.md + planner / implementer / researcher / reviewer
│   │   ├── workflows/               # hotfix / bug / feature / chore / research / merge-pr
│   │   ├── steps/                   # gates.sh / pr-create.sh / pr-merge.sh (sync-base is built into the runner)
│   │   └── routes.env               # class → model
│   ├── bin/run                      # Runner v1 (Python). Records go to workspace/runs/
│   └── tests/                       # Runner unittests
├── glue/                            # Section 4: connecting the sections
│   ├── README.md
│   ├── bin/intake                   # Free text → ticket (one LLM call)
│   └── bin/dispatch                 # todo → run. Logs go to workspace/logs/
├── console/                         # Web console and MCP server (local to the Mac, Python standard library)
│   ├── lib/core.py                  # Source of truth for reads and writes (shared by console and mcp)
│   ├── bin/console                  # HTTP server + JSON API
│   ├── bin/mcp                      # MCP server (stdio)
│   ├── static/                      # index.html / style.css / app.js (no build step)
│   ├── tests/                       # unittests
│   └── jobs/<id>/                   # Records of CLIs it started (not tracked; CONSOLE_JOBS relocates it)
├── .mcp.json                        # MCP registration for Claude Code (console/bin/mcp)
├── docs/
│   ├── ledger.md                    # Concept ledger (position, decisions, open questions, history)
│   ├── adr/                         # Design decisions (one per file, append only)
│   ├── sandbox-architecture.html    # Explanation of the sandbox's physical layout
│   └── aifactory-how-it-works.html  # How it works (single-page HTML)
└── website/                         # This documentation site (MkDocs)
    ├── mkdocs.yml  requirements.txt  README.md
    └── content/{ja,en}/
```

## Workspace (`AIFACTORY_WORKSPACE`)

```
workspace/                           # Default <repo>/workspace/. Not tracked by git
├── projects/<pj>/                   # ★The only place for project specifics (copy an example from examples/projects/)
│   ├── provision.sh                 # Template baking
│   ├── project.yml                  # Facts and policy
│   └── gates.sh                     # Quality gates
├── kanban/
│   ├── kanban.db                    # Source of truth for state (SQLite)
│   ├── tickets/<id>-<pj>-<slug>.md  # Source of truth for bodies
│   ├── attachments/<id>/<name>      # Source of truth for attachments (ADR-0040)
│   └── BOARD.md                     # Generated
├── runs/<date>-<pj>-<id>/           # Run records
│   ├── ticket.md  state.json
│   ├── prompt-<step>-<n>.md  agent-<step>-<n>.log  code-<step>-<n>.log
│   ├── agent-<step>-<n>.jsonl       # Raw events
│   └── work/                        # Collected artifacts
├── logs/
│   └── intake.log  dispatch.log
└── docs/                            # Notes on your own environment (hosts, power, token expiry, ...)
```

Project definitions are looked up in the order `workspace/projects/<pj>/` → `examples/projects/<pj>/`. `kb`, `intake`, `dispatch`, the runner and the console all use this order.

## Outside the repository (secrets and lending state)

| Path | Contents |
|---|---|
| `~/.config/sandbox/env` | CLI settings (`PVE_HOST` / `GW_SSH` and so on) |
| `~/.config/sandbox/pj/<pj>.env` | Per-project tokens and `GH_REPO` |
| `~/.config/sandbox/gh-app/` | GitHub App id and private key |
| `~/.config/sandbox/state.json` | VM lending table |
| `~/.ssh/conf.d/aifactory/` | Key and ssh settings for the VMs |
| `~/.local/bin/sandbox` | The copied CLI |
| `~/Library/LaunchAgents/com.aifactory.sandbox.gh-refresh.plist` | launchd job for token refresh |

## Git tracking policy

| Tracked | Not tracked |
|---|---|
| The mechanism (`sandbox/` `kanban/` `workflow/` `glue/` `console/`) | `workspace/` (ledger, tickets, run records, logs, project definitions) |
| `examples/projects/` (examples) | `*.env`, `*.token`, `.env*` |
| `docs/`, `website/content/` | `website/site/`, `website/.venv/` |
| Tests (`console/tests/`, `workflow/tests/`) | `console/jobs/`, `__pycache__/` |
| | `*.img`, `*.qcow2`, `*.tar.zst` |
