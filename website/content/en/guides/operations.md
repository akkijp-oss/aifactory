# Day-to-day operations

What this page tells you: the recurring tasks (tokens, pool, templates, firewall) and where to look when something breaks. The sources of truth are `sandbox/OPERATIONS.md` and `sandbox/STATUS.md`.

## Daily

```bash
kanban/bin/kb list                       # today's todo / review / blocked
sandbox ls                               # any lent VMs left behind?
sandbox gh-app status                    # is the App and its installations alive?
```

If a VM is lent with no run attached (yesterday's `--keep` or an interrupted run), return it with `sandbox release <id>`.

## Tokens

### Claude Code token (per project)

Long-lived tokens from `claude setup-token` expire. When one does, agent steps fail with an authentication error and the ticket becomes `blocked`.

```bash
claude setup-token
sandbox token rotate                     # update the global file, every project file holding the key and ctl.env, then restart the console and reinject --all
sandbox token set <pj>                   # save one project only
sandbox reinject <id>                    # push it into a VM that is already lent (no rollback)
sandbox reinject --all
```

Run `sandbox token rotate` on the control plane (the host that has `~/.config/aifactory/ctl.env`). The day each token was saved is recorded as a comment in the file, so `sandbox token show` can tell you how many days ago that was.

### GitHub token (automatic)

GitHub App installation tokens expire after one hour. The launchd job `com.aifactory.sandbox.gh-refresh` reissues them to every lent VM every 45 minutes, and the runner reissues before each code step. By hand:

```bash
sandbox gh-app refresh                   # every lent VM
sandbox gh-app token <pj>                # check issuance (prints the token)
```

When you add permissions to the App (such as Actions: Read), each installation must approve them. After approval the CLI requests them automatically.

## Pool

| Task | Command |
|---|---|
| Lending status | `sandbox ls` |
| `take` fails with no free VM | Check `sandbox ls` → `release` what is not needed. If still short, grow the pool |
| Grow the pool | `TPL_VMID=911x sandbox/proxmox/run.sh 40-pool.sh <pj> <count>` → `50-firewall.sh`. Watch `data%` in `lvs pve/data` |
| Rebuild a dirty VM | `qm destroy <vmid>` → `40-pool.sh`. A VM without `clean` cannot `reset` |
| Never touch lent VMs | Read `~/.config/sandbox/state.json` and skip them (`50-firewall.sh` accepts `LENT=`) |

## Updating templates

| Layer | When | Steps |
|---|---|---|
| Project layer (new dependencies, seed changes) | When the project's Gemfile / package.json changes | Rebake with `32-pj-template.sh` → rebuild the pool with `40-pool.sh` → `50-firewall.sh` |
| Base layer (OS packages, tools, Claude Code version) | Monthly | `30-base-template.sh` → 32 → 40 → 50 for every project. Doing it more often makes rebuilding project layers a burden, hence monthly |

Check `sandbox ls` for lent VMs before rebuilding.

## Egress limits (firewall)

VMs can only reach the internet and the DNS on sb-gw (ADR-0010). The LAN, the Proxmox host, neighbouring VMs and the tailnet are unreachable. After rebuilding templates or pools, run `50-firewall.sh` again so the `clean` snapshots include the firewall settings.

Check (take a generic VM and test from inside):

```bash
sandbox take generic 999
sandbox ssh 999 'curl -sI https://github.com | head -1; ping -c1 -W1 <IP of a host on your LAN> || echo "LAN unreachable (as expected)"'
sandbox release 999
```

## Failures and fixes

| Symptom | Look at | Fix |
|---|---|---|
| `task-xxx.sb.internal` does not resolve | `dig sb-gw.sb.internal`, Tailscale split DNS | Has split DNS disappeared? `ssh root@10.77.0.2 systemctl status dnsmasq` |
| Cannot ping 10.77.0.2 | Route approval in the Tailscale console, `pct exec 9000 -- journalctl -u tailscaled -n 20` | Add the ACL grant if missing. In a hurry, `SB_JUMP=<same value as PVE_HOST>` goes through the Proxmox host |
| Cannot ssh to a VM | `qm status 92NN`, `qm agent 92NN network-get-interfaces` | `qm start`. If it is up, use `qm terminal` for the serial console |
| `reset` fails | Does `qm listsnapshot 92NN` show `clean`? | If not, destroy and rebuild with `40-pool.sh` |
| VM has no internet | `iptables -t nat -S \| grep 10.77`, `pve-firewall status` | Reapply SDN with `pvesh set /cluster/sdn`. LAN / other VMs / tailnet are unreachable by design |
| Mac cannot reach a VM (after enabling the firewall) | `/etc/pve/firewall/<vmid>.fw`, `qm config <vmid> \| grep firewall` | Rerun `50-firewall.sh` |
| Claude Code authentication error | `env \| grep CLAUDE_CODE_OAUTH_TOKEN` inside the VM; the age shown by `sandbox token show` | `claude setup-token` → `sandbox token rotate` (every project, `ctl.env` and re-injection in one command) |
| The Proxmox host is down | `ssh $PVE_HOST` fails, `pvecm nodes` (from another node if clustered) | Power it on (WoL / IPMI / the physical button). The pool has onboot=0, so `qm start` by hand |

## Periodic maintenance

- Monthly: OS update of the base template
- Renew with `claude setup-token` → `sandbox token rotate` as the token's expiry approaches (watch the age in `sandbox token show`)
- When `workspace/runs/` grows, delete or archive old runs (`state.json` and `work/` are enough as records)
- When `done` items pile up in `workspace/kanban/BOARD.md`, look back with `kb list --all` and then stop worrying (the DB is small)
