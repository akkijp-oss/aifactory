# Security policy

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's private vulnerability reporting on this repository ("Security" tab → "Report a vulnerability"). You will get an acknowledgement within a week.

## What is in scope

- The Mac-side CLIs (`sandbox`, `kb`, `run`, `intake`, `dispatch`), the Web console and the MCP server
- The Proxmox-side scripts under `sandbox/proxmox/` (network, firewall, token handling)
- Token handling: Claude Code long-lived tokens and GitHub App installation tokens are injected into a tmpfs inside the VM at `sandbox take` time and must never be written to disk in a template (see ADR-0005 / 0006 / 0008)

## Layers that keep secrets out of this public repository

1. Layout: operational data (project definitions, tickets, run records, private notes) lives in `AIFACTORY_WORKSPACE`, never in the repository (ADR-0016). Tokens live in `~/.config/sandbox/`
2. `.gitignore`: env files, keys, certificates, the workspace
3. Local hooks (`bin/install-hooks.sh`): pre-commit scans staged changes (secret patterns, private hostnames and project names, forbidden paths); pre-push runs gitleaks on the pushed range and `bin/oss-check.sh`
4. Sandbox gate: `examples/projects/aifactory/gates.sh` runs `bin/oss-check.sh` inside the VM before an agent's branch can be pushed
5. CI: a `secrets` job runs gitleaks over the full history on every push and pull request
6. GitHub: secret scanning and push protection are enabled on the repository and as the organization default

If a secret does land in history, rotate it first, then rewrite history; contact the maintainer via private vulnerability reporting.

## Design notes that matter for security

- The Web console binds to 127.0.0.1 only and has no authentication. Do not expose it (ADR-0013).
- Sandbox VMs can reach the internet and the gateway's DNS only; LAN, other VMs and the tailnet are blocked by the Proxmox firewall (ADR-0010).
- Agents run inside the VM with `--dangerously-skip-permissions`. The VM is the security boundary, not the agent.
- `workspace/` (tickets, run logs, project definitions) may contain private code and must stay git-ignored.
