# Requirements

What this page tells you: the hardware, accounts and software needed to run the factory, and what each one is for.

## Overview

```mermaid
flowchart LR
  subgraph Mac["Mac (control machine)"]
    CLI[sandbox / kb / intake / dispatch / run]
  end
  subgraph Home["Network with the Proxmox host"]
    PVE[Proxmox host<br>PVE_HOST]
    GW[sb-gw LXC<br>Tailscale + DNS]
    VM[VM pool 10.77.0.0/16]
    PVE --- GW --- VM
  end
  subgraph Cloud["External services"]
    TS[Tailscale]
    GH[GitHub + GitHub App]
    CL[Anthropic (Claude Code)]
  end
  CLI -- tailnet --> GW
  CLI -- ssh --> PVE
  VM -- push / PR --> GH
  VM -- claude -p --> CL
  CLI -.approve.-> TS
```

## Hardware

| Item | Requirement | Purpose |
|---|---|---|
| Proxmox VE host | 9.x, LVM-thin local storage. RAM: "8 GB per VM × number of VMs plus headroom" (about 100 GB for 3 projects × 3 VMs + 3 generic VMs) | Hosts the VM pool. It is addressed by `PVE_HOST` (an ssh alias) in `~/.config/sandbox/env` |
| Mac | macOS, ssh, `python3` 3.10 or later, `jq`, `gh`, `curl`, `openssl` | Every CLI runs here. The runner is Python, `sandbox` is bash |
| Network | Ability to create an SDN (simple zone) on Proxmox, with outbound SNAT | Gives the VMs their own address space `10.77.0.0/16` (changeable with `SB_NET`) |

!!! warning "Proxmox host power"
    If the host goes down, the whole VM pool goes with it. Confirm beforehand that you can power it on remotely (Wake-on-LAN, IPMI, and so on). Pool VMs have `onboot=0`, so after recovery start them by hand with `qm start`.

## Accounts and services

| Service | What you need | Purpose |
|---|---|---|
| Tailscale | One tailnet, permission to approve subnet routes in the admin console, and an API key if you need to edit the ACL | The path from the Mac to the VMs. Only the gateway LXC joins the tailnet and advertises `10.77.0.0/16` |
| GitHub | Admin rights on the target repositories (to install a GitHub App) | The GitHub App issues one-hour tokens that let VMs push, open PRs and merge |
| Anthropic | A plan that includes Claude Code. One long-lived token per project via `claude setup-token` | Authentication for `claude -p` inside the VM |

## Software (Mac side)

| Tool | Version | Check |
|---|---|---|
| Python | 3.10 or later (standard library plus `pyyaml` and `jsonschema`) | `python3 -c "import yaml, jsonschema"` |
| Claude Code | Latest | `claude --version` |
| GitHub CLI | 2.x | `gh --version` |
| jq | 1.6 or later | `jq --version` |
| ssh / scp | OpenSSH | `ssh -V` |

Everything installed on the Proxmox side and inside the VMs is handled by the build scripts ([Build the sandbox](build-sandbox.md)).

## Target projects

A repository (project) joins the factory when its three files (`provision.sh` / `project.yml` / `gates.sh`) are placed in `$AIFACTORY_WORKSPACE/projects/<pj>/`. One example ships with the repository.

| Project | Repository | Stack | Location |
|---|---|---|---|
| kumitate | [akkijp/kumitate](https://github.com/akkijp/kumitate) | pnpm 10 monorepo + Next.js + PostgreSQL 16 (pgvector) + drizzle + vitest | `examples/projects/kumitate/` |

To add a project see [Add a project](../guides/add-project.md).

## Running it in another environment

Four things need replacing.

1. **Proxmox host**: `PVE_HOST` (an ssh alias, required) and `GW_SSH` (the ssh target of the gateway LXC, required) in `~/.config/sandbox/env`. The Proxmox-side scripts take the node name from `SB_NODE` (default: the host's own hostname)
2. **Address space and VMIDs**: defaults are `SB_NET=10.77` (the /16 prefix), `SB_GW_CT=9000`, `SB_BASE_VMID=9100`, `SB_POOL_BASE=9200`; on the Mac side `SB_POOL_NET=10.77.1`. If you change them, keep the Proxmox side and the Mac side in sync (the "naming, numbering and addresses" section of `sandbox/README.md`)
3. **GitHub App**: create one under your own account with `sandbox/bin/gh-app-setup`
4. **Project definitions**: `$AIFACTORY_WORKSPACE/projects/<pj>/` (provision.sh / project.yml / gates.sh)

Everything else (kanban / glue / workflow) is environment independent.
