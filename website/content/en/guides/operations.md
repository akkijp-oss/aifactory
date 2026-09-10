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

### Claude keys (the key pool)

The Claude keys that agents use inside VMs live in the control plane's **key pool** (ADR-0044 / ADR-0045). The console's *Keys* screen (`#/keys`) and `sandbox keys` are two doors to the same `~/.config/sandbox/keys.json`. Each key says whether it is used for Fable (planning, design and review steps) and/or for Opus, Sonnet and Haiku (implementation and research steps); when a ticket runs, one key per model is picked (the one that has gone longest without being used). Register several keys and the usage windows are spread across them.

```bash
claude setup-token                                   # produces the value (sk-ant-oat01-…)
sandbox keys add max-akki --fable --other --note "akki's Max plan"   # value typed in without echo, or piped on stdin
sandbox keys list                                    # registered keys, last use, use count
sandbox keys set max-akki --disable                  # stop using it (lent VMs get another key reinjected)
sandbox keys token max-akki                          # replace only the value (when it expires)
sandbox reinject <id>                                # push the current key into a VM that is already lent
```

Long-lived tokens from `claude setup-token` expire. When one does, the step stops with `failure: key` and the ticket becomes `blocked`. The issue date in `sandbox keys list` tells you when that is coming.

**The pool is the only source of the keys a VM receives** (ADR-0060). `sandbox token set … claude` refuses and saves nothing, a `CLAUDE_CODE_OAUTH_TOKEN` line left in `~/.config/sandbox/env` or `pj/<pj>.env` is ignored (`sandbox token show` lists it as `[stale]` with the `sed` line that removes it), and there is no fallback to those files when the pool is empty. `sandbox token rotate claude` replaces only the key intake uses (`ctl.env`) and restarts the console; it does not touch the pool. Pool keys are replaced with `sandbox keys token <name>`.

**When the pool has no key for a purpose the run needs, the run pauses** (ADR-0046). No VM is taken, the ticket goes back to todo and its note says it is paused for lack of a key. Register a key on the *Keys* screen and the 5-minute timer starts the run over. Disabling every key stops the factory; enabling one resumes it.
### Running out of usage (the usage limit) resumes by itself

When the token's **usage limit** (the 5-hour or 7-day window) is used up, `claude -p` is rejected and the agent step stops. The runner treats this apart from an ordinary failure: it commits whatever was changed so far as `wip: usage limit`, pushes it to the wip branch, and puts the ticket **back to todo** (the note says it is paused and when the limit is expected to reset). The control plane's systemd timer `aifactory-resume.timer` calls `dispatch --resume-paused` every 5 minutes, and once the reset time has passed it **continues** the run with `kb run <id> --from` (the same step, on top of the wip branch). Nobody has to do anything (ADR-0043).

```bash
kb resumable                             # paused tickets and when each can continue
dispatch --resume-paused                 # do it now (what the timer does)
journalctl -u aifactory-resume           # timer log; runs also land in workspace/logs/dispatch.log
kb run <id> --from                       # continue by hand (the command is in the note)
```

- When no reset time was recorded (the CLI output had no `rate_limit_event`), the retry happens `AIFACTORY_RESUME_BACKOFF_MIN` minutes (default 30) after the stop
- After `AIFACTORY_RESUME_MAX_HITS` stops in a row (default 6) the automatic resume gives up and the ticket becomes `blocked` (the token's window is too small, etc.). Both can be set in `ctl.env`
- If the token itself is invalid, expired or out of credit (`failure: key`), waiting does not help, so the ticket is `blocked`. Fix the token as above and continue with `kb run <id> --from`

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
| Claude Code authentication error (`failure: key`) | `key=… (pool: <name>)` in the run log says which key; the issue date in `sandbox keys list` | `claude setup-token` → `sandbox keys token <name>` (or the *Keys* screen). Intake's key: `sandbox token rotate claude`. Continue the stopped run with `kb run <id> --from` |
| A step stopped at the usage limit (ticket back to todo, note says paused) | `kb resumable`, `journalctl -u aifactory-resume` | Nothing. Once the reset time passes the timer continues the run. In a hurry, add another key on the *Keys* screen and run `dispatch --resume-paused` |
| The Proxmox host is down | `ssh $PVE_HOST` fails, `pvecm nodes` (from another node if clustered) | Power it on (WoL / IPMI / the physical button). The pool has onboot=0, so `qm start` by hand |

## Periodic maintenance

- Monthly: OS update of the base template
- Renew with `claude setup-token` → `sandbox keys token <name>` as a key's expiry approaches (watch the issue date in `sandbox keys list`); intake's key in `ctl.env` with `sandbox token rotate claude`
- When `workspace/runs/` grows, delete or archive old runs (`state.json` and `work/` are enough as records)
- When `done` items pile up in `workspace/kanban/BOARD.md`, look back with `kb list --all` and then stop worrying (the DB is small)
