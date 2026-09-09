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

### Claude key pool (kept on the control plane)

When you hold several keys, name them and keep them in `~/.config/sandbox/keys.json` on the control plane instead of handing them out per project. Lending a VM (`take`) then picks one key per model family.

```bash
sandbox keys add fable-main --fable      # a key from a Fable contract (the value is typed in)
sandbox keys add opus-a --other          # for Opus / Sonnet / Haiku
sandbox keys list                        # names, flags, last 4 characters, last use, and the leases holding each key
sandbox keys set opus-a --disable        # stop using it (switch the leases over with reinject)
```

The key picked is the eligible one that has gone longest without being used. The same ticket keeps its key across `reinject`, and only picks again once that key can no longer be used. A family with no candidate falls back to the per-project keys above, so an empty pool behaves exactly like today. The console's *Keys* screen does the same things. See [the keys section of the sandbox CLI](../reference/cli-sandbox.md) and ADR-0043.

### GitHub token (automatic)

GitHub App installation tokens expire after one hour. The systemd timer `aifactory-gh-refresh.timer` on the control plane reissues them to every lent VM every 45 minutes, and the runner reissues before each code step. By hand:

```bash
sandbox gh-app refresh                   # every lent VM
sandbox gh-app token <pj>                # check issuance (prints the token)
```

When you add permissions to the App (such as Actions: Read), each installation must approve them. After approval the CLI requests them automatically.

## Pool

| Task | Command |
|---|---|
| Lending status | `sandbox ls` |
| Compare the defined and actual pool sizes | `sandbox status [pj]` (`PJ DEFINED ACTUAL LENT FREE`). The console sandbox screen and the `sandbox_status` MCP tool show the same four numbers |
| `take` fails with no free VM | Read the breakdown in the error (defined / actual / lent / not built / no clean snapshot) → `release` what is not needed; grow the pool if the actual size is short |
| Grow the pool | `TPL_VMID=911x sandbox/proxmox/run.sh 40-pool.sh <pj> <count>` → `50-firewall.sh`. Watch `data%` in `lvs pve/data` |
| Rebuild a dirty VM | `qm destroy <vmid>` → `40-pool.sh`. A VM without `clean` cannot `reset` |
| Never touch lent VMs | Read `~/.config/sandbox/state.json` and skip them (`50-firewall.sh` accepts `LENT=`) |

### VMs nobody uses stop by themselves

A pool VM that is not lent out becomes a **stop candidate** 24 hours (default) after it was last used. Only when there are more than 10 candidates (default; the cutoff) are the oldest ones beyond that number stopped; with 10 or fewer candidates nothing is stopped. The systemd timer `aifactory-idle-stop.timer` on the control plane calls `sandbox idle-stop` every 15 minutes (ADR-0033 / ADR-0035). A `stopped` STATUS in `sandbox ls` is therefore usually power saving, not a fault; the table is followed by `[idle-stop] N 台が節電で停止中`.

**You do not need to start them by hand.** The next `take` does it and logs `[start] vm <vmid>: 停止中だったので起動した（N 秒）` (30–60 seconds extra).

```bash
sandbox idle-stop --dry-run              # see what would be stopped, without stopping it
journalctl -u aifactory-idle-stop        # the timer's log
```

To keep VMs running, put `SB_IDLE_STOP_HOURS=0` in `~/.config/sandbox/env` (everything) or in `~/.config/sandbox/pj/<pj>.env` (that project only). To change the window, write the number of hours instead of `24`; to change the cutoff, set `SB_IDLE_STOP_KEEP=<count>` (global only; `0` stops every candidate).

## Updating templates

| Layer | When | Steps |
|---|---|---|
| Project layer (new dependencies, seed changes) | When the project's Gemfile / package.json changes | Rebake with `32-pj-template.sh` → rebuild the pool with `40-pool.sh` → `50-firewall.sh` |
| Base layer (OS packages, tools, Claude Code version) | Monthly | `30-base-template.sh` → 32 → 40 → 50 for every project. Doing it more often makes rebuilding project layers a burden, hence monthly |

Check `sandbox ls` for lent VMs before rebuilding.

## Promoting develop to main (by a human)

aifactory's own PRs target `develop`. A PR with green gates, a PASS review and all CI checks passing is merged into `develop` by the runner ([`auto_merge`](../reference/project-yml.md), ADR-0042). **Promotion to `main` is done by a human.**

```bash
gh pr create --base main --head develop --title "develop -> main" --body "Automatically merged runs: #.. #.."
gh pr checks <number> --watch
gh pr merge <number> --merge
```

- Promote when `develop` is green and you have run one round on real hardware. Once a day is enough unless something is urgent
- Never promote a red `develop`. Land the fix on `develop` first
- Deployment to the control plane (`bin/ctl-update`) still follows `origin/main`. Use `bin/ctl-update --ref origin/develop` only to try out `develop`, then go back with `bin/ctl-update`

## Changing a project definition

The project definitions the runner reads (`project.yml` / `gates.sh` / `provision.sh` under `examples/projects/<pj>/`) are read straight from the working tree of the control plane's checkout. Editing `gates.sh` there takes effect from the next gates run, but the control plane's remote is https, so `git push` fails. Leave it like that and production runs from a checkout that differs from origin.

The rule is: **edit and push from your own (Mac) checkout, and only deploy on the control plane with `bin/ctl-update`.** If you did edit on the control plane in a hurry, carry the commits out with `git format-patch origin/main --stdout`, push them from your machine, then bring the control plane back in line with `git reset --hard origin/main`.

Any difference (unpushed commits, commits not pulled in, uncommitted changes) is shown as a warning on the console board. The full procedure, including the deploy key swap that lets the control plane push directly, is in `sandbox/OPERATIONS.md` under 「PJ 定義の変更手順」.

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
