# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `examples/projects/aifactory/`: aifactory as its own sandbox project (public, tokenless clone, `mkdocs serve` on :3000 as the app, gates = CI set + `bin/oss-check.sh`). Anyone can run a ticket end to end with it; the maintainers use it for dogfooding
- `console/bin/install.sh --launchd` bakes `AIFACTORY_WORKSPACE` into the plist, so a workspace outside the repository also works for the resident console

### Fixed
- Runner: when the wip-branch push fails (for example the GitHub App lacks a permission), the diff is saved to `wip.patch` in the run directory instead of being lost with the VM rollback
- GitHub App manifest now requests `workflows: write`; without it GitHub rejects pushes that touch `.github/workflows/`
- `console/bin/install.sh`: a fullwidth parenthesis right after `$ws` was parsed as part of the variable name

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
