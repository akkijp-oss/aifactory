# Security policy

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's private vulnerability reporting on this repository ("Security" tab → "Report a vulnerability"). You will get an acknowledgement within a week.

## What is in scope

- The Mac-side CLIs (`sandbox`, `kb`, `run`, `intake`, `dispatch`), the Web console and the MCP server
- The Proxmox-side scripts under `sandbox/proxmox/` (network, firewall, token handling)
- Token handling: Claude Code long-lived tokens and GitHub App installation tokens are injected into a tmpfs inside the VM at `sandbox take` time and must never be written to disk in a template (see ADR-0005 / 0006 / 0008)

## Design notes that matter for security

- The Web console binds to 127.0.0.1 only and has no authentication. Do not expose it (ADR-0013).
- Sandbox VMs can reach the internet and the gateway's DNS only; LAN, other VMs and the tailnet are blocked by the Proxmox firewall (ADR-0010).
- Agents run inside the VM with `--dangerously-skip-permissions`. The VM is the security boundary, not the agent.
- `workspace/` (tickets, run logs, project definitions) may contain private code and must stay git-ignored.
