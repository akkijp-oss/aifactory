# Web console

What this page tells you: how to see where the factory is from a browser and do the same operations with buttons. Without memorising the CLI, the board, the logs of a running run, VM lending, filing and dispatch all fit on one screen.

## Start it

```bash
console/bin/console --open        # opens http://127.0.0.1:8765/ in the browser
console/bin/console --port 9000   # change the port
```

- Runs on the Python 3 standard library alone. No npm, no pip (use the same `python3` as the runner)
- By default it binds to 127.0.0.1 only, with no authentication. On an internal network address (10.x / 192.168.x / 100.64.x and other non-global addresses, such as the control-plane LXC) `--host <IP>` (or `CONSOLE_HOST`) is enough and no passphrase is needed; the tailnet and the firewall are the boundary. To require one anyway, set `CONSOLE_TOKEN` (one visit to `/?token=<passphrase>` sets a cookie). Binding to 0.0.0.0 or a global address requires the passphrase
- Stop it with ++ctrl+c++. Jobs it started (such as `kb run`) keep running, and reappear in the list the next time the console starts

To keep it running, register it with launchd. It starts at login and restarts if it dies.

```bash
console/bin/install.sh --launchd   # register; also creates ~/.local/bin/aifactory-console
console/bin/install.sh --remove    # unregister
launchctl kickstart -k gui/$(id -u)/com.aifactory.console   # restart after changing the code
```

launchd's PATH is minimal, so the installer writes the absolute path of the current shell's `python3` and the locations of `claude` / `gh` / `jq` / `sandbox` into the plist. The log is `~/Library/Logs/aifactory-console.log`.

## Screens

```mermaid
flowchart LR
  B[Board<br>pipeline strip + 5 columns] --> T[Ticket<br>body, history, run]
  B --> L[Tickets<br>filter by number, title, project, state]
  L --> T
  T --> R[Runs<br>step track + logs]
  B --> I[File<br>intake / kb new]
  B --> D[Dispatch<br>dialog → dispatch]
  D --> J
  I --> J[Jobs<br>follow CLI output]
  T --> J
  S[sandbox<br>lending and projects]
```

| Screen | What you see | What you can press |
|---|---|---|
| Board | The pipeline strip (todo → in progress → review → done, with "waiting for a human" to the side) and five columns of cards in the same order. Running runs appear under "in progress" with the current step and elapsed time. The strip, the columns and the runs all follow the project filter, and the scope is stated above the strip | File a ticket (goes to File), dispatch (a dialog to pick project, count and dry run; it shows the ticket that will be picked before you confirm, and cannot be pressed when there is no todo), *Search the list* and the done column's *See all n more* (both go to Tickets) |
| Tickets | Every ticket in one table, newest update first (number, project, kind, title, state, PR, updated). The board's done column only keeps the newest 15, so anything past that is found here. The filter lives in the URL, so it survives opening a ticket and coming back | Narrow by number (prefix), title (substring), project and state, all at once. Pressing a row opens the ticket |
| Ticket | Body (Markdown), state history, related runs and jobs | `kb run` (a dialog shows project, workflow and expected duration first; dry run / `--keep` / `--resume`. On a done ticket the button is disabled and *Back to todo (redo)* sits beside it; when the project has no project.yml there is no run button at all, only a note on where to put the file), move the state (start / review / done / back to todo / waiting for a human; no confirmation, the toast offers *Undo*), fix the kind, PR number and note, sync the state from the run record (before it runs you see the target run and the state and note before and after; a warning if the ticket was updated after that run) |
| Runs | The list of `workspace/runs/` and, per run, the step track (pass / fail and duration per step, loops ↺, terminal end / human). File list and logs. Opening a run leads with an *Outcome* panel: one sentence each for the result, the step it stopped at with the failing gates, and the ticket's current state; when the record does not say, it says so. A run that ended at `human` with a wip branch left behind also gets one line with the command to continue it (`kb run <id> --from <step> --branch <branch>`). Once a human has closed the run out (opened a PR from the wip branch and merged it, or given up), it says so instead — who recorded it, when, and what they wrote — and the continue command is gone. A run the runner merged by itself reads "aifactory merged PR #n into develop (done).", and a run that did not meet the conditions still reads "a PR was created" with one more line saying why the automatic merge did not happen. Files are split into artifacts (report, plan, gate results), step logs, and everything else (collapsed). **A run whose runner is gone is listed as 中断 (abandoned), not as running**, together with the end time, state and exit code of the job that ended, and a note that waiting will not help. A run that failed while preparing the VM (`sandbox take`) shows a summary of the error. A run that is merely missing records is no longer called a "v0 record": it says which times and steps are unknown | Read the reason, read the report (both open the file in place), open the job, open the ticket, see sandbox (with a note when the VM is still on loan), sync the state from the run record (only when the ledger's run is this run and the ticket is still in progress) |
| sandbox | Lent VMs (task, VM name, IP, project, time since lending, app URL), the project list (whether project.yml and the token file exist, and the pool as defined / actual / lent / free — actual being the count from the last successful `sandbox ls`, with its fetch time in the heading and, past 10 minutes, how long ago it was; a row whose actual size is below the defined one gets a note with how many VMs are missing and how to add them), and the pool VM list (lent to, VM name, IP, project, power state, lent since). Lending and power are separate axes: returning a VM does not stop it right away, so running VMs are listed even when nothing is lent. A VM that has gone unused for the configured window (24 hours by default) is tagged *停止候補* (stop candidate); once candidates exceed the cutoff (10 by default) the oldest are stopped to save power and shown as *節電で停止中* with a note that the next lending starts it again (ADR-0033 / ADR-0035). The heading shows when the list was fetched and distinguishes fetching / failed (last lines of the reason plus a link to the job) / never fetched. When the same VM is lent to more than one ticket (the ledger holds two or more entries with the same vmid), leases and VMs are counted separately (*2 leases, 1 VM*), each shared VM gets a warning listing the tickets, and the rows are marked *shared*. Pool usage counts VMs, not leases. A VM without a `clean` snapshot cannot be seen in `sandbox ls`, so it still counts as free here and only shows up in the `take` error | `sandbox ls` (ssh to Proxmox, a few seconds), `sandbox release` (if a run is active on that VM, or the VM is shared with another ticket, you must type the ticket number first; for a shared VM the dialog first names the work that the rollback destroys and the ledger rows that stay behind) |
| File | The draft you are typing (request text, project, kind, dry run, title, body, PR number). It survives a detour to another screen, a reload and the browser's back button (within the same tab only; closing the tab discards it). When a draft is restored, a line at the top of the screen says so | The left panel has an LLM turn your prose into a title and completion criteria; the right one files the title and body you wrote as they are (*File from prose* and *Write the title and criteria yourself*; the CLIs behind them are `intake` and `kb new`). Either way filing only records the ticket — nothing runs yet, which the screen says at the bottom before pointing at a ticket's *Run* and the board's *Dispatch*. Picking a kind shows when to choose it right there, and the body field carries a `## 背景` (background) / `## 完了条件` (completion criteria) skeleton as a placeholder. Dispatch lives on the board. *Discard the draft* clears it explicitly (the toast offers *Undo*). On a successful submit, only that panel's draft is cleared. Picking a project shows, right under the select, whether that project has a project.yml (a *Ready to run* / *Setup needed* badge plus an explanation; when setup is needed it names the file to write and links to the sandbox screen). Filing itself is never blocked |
| Jobs | The CLI processes this console started. Output is followed every 2 seconds. A finished job shows *What to do next* (open the created ticket, sync the state of a stopped run, and so on) plus one line with the job's end time and the ticket's current state. When the ticket has moved on since the job (done, or edited later), the wording is past tense and the primary button becomes *Open the ticket* | Stop (SIGTERM to the process group) |
| Logs | The intake and dispatch records in one table (time, action, project, ticket, result, reason; newest first), with column names and plain wording instead of `rc=` and unlabelled numbers. *Show the raw log* below the table keeps the original `workspace/logs/intake.log` / `workspace/logs/dispatch.log` text. The log format itself is unchanged; the console derives the columns (ADR-0026) | Narrow by ticket number, project and kind (intake / dispatch), all ANDed, with the filter kept in the URL. The ticket number is a link to that ticket |
| Settings | Workflow steps, the model routes (`routes.env`), `git status` | — |

## Following a running run

Below the step track, *Definition:* lists the steps of the workflow in order. Step ids are bare English words, so the *What each step does* fold under it explains every step: what it does, the workflow-specific instruction (the `brief` in the yml), the files it reads and writes, and where a failure sends it back to (and after how many loops it waits for a human). Hovering a step in the list shows the same explanation.

While a run is in progress its screen refreshes every 5 seconds and automatically opens **the log of the step that is running** (the `agent-*.log` / `code-*.log` named by `current` in `state.json`). For agent steps the runner turns the `claude -p` event stream into a readable log:

```
[+00:06] ▶ Read: /home/dev/app/hello.txt
  ↳
    1	hello, factory
[+00:14] ▶ Write: /home/dev/work/990/research.md
  ↳
    File created successfully at: …
[+00:18] result: success turns=5 duration=15s cost=$0.04
```

`▶` is a tool call, `↳` the first three lines of its result, and the timestamp is elapsed time since the step started. Once you pick a different file, that run stops switching automatically.

## Rules

- **State is only changed through the existing CLIs** (`kb` / `intake` / `dispatch` / `sandbox`). The console opens `kanban.db` read-only and never writes `state.json`. It follows the rules in [Working with multiple sessions](multi-session.md)
- **Long operations are jobs.** `kb run` can take more than an hour, so the console detaches it as a child process and streams its output to `console/jobs/<id>/log` (not tracked by git)
- **Duplicates are rejected.** A second `kb run` for the same ticket, a second `dispatch`, or a `release` for a task with a running job is refused inside a lock
- **Readable files are limited** to the workspace (`runs/`, `kanban/tickets/`, `logs/`, `projects/`), `examples/projects/`, `workflow/kit/` and `console/jobs/`. Token contents are never shown
- **Confirmation scales with risk.** Moving a ticket's state applies immediately and the toast offers *Undo*. Real runs and dispatch open a dialog that shows what will happen (the ticket to be picked, the project, the expected duration). Syncing the state overwrites the ticket, so the state and note before and after are shown first. Releasing a VM and stopping a job use a danger-styled dialog, and when a run is active on that VM you must type the ticket number
- **Dates and times are shown in the browser's time zone.** Records carry a UTC offset (`2026-09-08T00:21:00+00:00`), and an elapsed time is only the difference between the record and now, so it reads the same in any time zone and does not jump when a run finishes. The bottom of the navigation names the time zone being used for display and says so when the records are kept in a different one. A field with no recorded time reads 時刻の記録なし instead of being blank. The decision is ADR-0026 ([Design decisions](../decisions/index.md))
- Navigation is ordered by how often each screen is used (board / file / runs / jobs / sandbox / logs / settings). Press ++g++ then a letter (++b++ board, ++i++ file, ++r++ runs, ++j++ jobs, ++s++ sandbox, ++l++ logs, ++c++ settings) to jump; ++question++ lists the shortcuts
- The rules for UI text (buttons are verbs, sentences are polite, one glossary) live in `console/UX.md` in the repository. The decision record is ADR-0019

## API

The JSON API the screens use can be called with `curl`. POST requires the `X-Console: 1` header (other sites in the browser cannot add it, which prevents accidental operations).

```bash
curl -s localhost:8765/api/overview
curl -s localhost:8765/api/tickets/204
curl -s -H 'Content-Type: application/json' -H 'X-Console: 1' -X POST localhost:8765/api/tickets/204/run -d '{"dry_run": true}'
```

| Method and path | What it does |
|---|---|
| `GET /api/overview[?pj=]` | Counts per state, running runs and jobs, number of lent VMs. `pj` narrows the run lists only, before the `limit` is applied (so a project's run is never dropped when seven or more are running; `runs_active_n` is the count after filtering). `counts` always covers every project |
| `GET /api/tickets[?pj=]` / `GET /api/tickets/<id>` | List / body, history, runs, jobs. The list also returns the project candidates (`pjs`) and whether each one has a project.yml (`pj_ready`) |
| `GET /api/next[?pj=]` | The todo `kb next` would pick for dispatch, or `null` |
| `GET /api/tickets/<id>/sync-preview[?run=]` | A preview of the state sync (`kb sync --dry-run`): state and note before and after, and whether the ticket was updated after that run |
| `POST /api/tickets` | `kb new` |
| `POST /api/tickets/<id>/action` | `{action: start / review / done / reopen / block / set / sync, note, kind, pr, dry_run}`. `sync` returns the before/after without writing unless you pass `dry_run: false` |
| `POST /api/tickets/<id>/run` | `kb run` as a job. `{dry_run, workflow, keep, resume, from_step, from_branch, wait}` (`from_step` redoes a run that ended at `human` on a new VM, continuing from where it stopped; an empty string leaves the step to the record) |
| `GET /api/runs` / `GET /api/runs/<name>` | Run records |
| `POST /api/runs/<name>/action` | Record a human closeout on a run (`kb run-note`). `{action: close / note, result: done / abandoned, pr, text}`. `close` records the outcome for the first time; `note` rewrites the text of an existing record |
| `POST /api/tickets/<id>/attach` | Add attachments. Send `files` as `multipart/form-data` (several at a time). JSON is not accepted |
| `POST /api/tickets/<id>/detach` | Remove one attachment named `{name}`. The file is deleted, so this cannot be undone |
| `GET /api/tickets/<id>/attachments/<name>` | Serve one attachment. Only images (png / jpg / gif / webp) are shown inline; everything else is always a download. Content sniffing is always off (`X-Content-Type-Options: nosniff`) |
| `GET /api/file?path=&tail=` | A file under one of the allowed roots |
| `GET /api/sandbox` / `POST /api/sandbox/ls` / `POST /api/sandbox/release` | Lending, refresh the list, release `{task}` |
| `POST /api/intake` / `POST /api/dispatch` | `{text, pj, kind, dry_run}` / `{pj, once, max, dry_run}` |
| `GET /api/jobs` / `GET /api/jobs/<id>?offset=` / `POST /api/jobs/<id>/stop` | Job list, follow output, stop |
| `GET /api/logs` / `GET /api/config` | intake / dispatch logs / workflows, routes and git |

## Using it from an AI session (MCP)

`.mcp.json` carries two servers: **`aifactory-local`** (the workspace on this machine) and **`aifactory-ctl`** (the control plane on Proxmox). With the control plane in an LXC on Proxmox ([Tenants](tenants.md)), use `aifactory-ctl`, or register it at user scope so it works from any directory (`claude mcp add --scope user aifactory -- <repo>/console/bin/mcp-remote`). Codex CLI: `codex mcp add aifactory -- <repo>/console/bin/mcp-remote`. Any client that speaks stdio MCP can use the same entry point. `console/bin/mcp-remote` starts the MCP server inside the LXC over ssh, so the AI session reads and writes the LXC's workspace and lending state directly. The target comes from `~/.config/aifactory/mcp-remote.env`: `AIFACTORY_CTL` (default `aifactory@ctl.main.sb.internal`; another tenant is `aifactory@ctl.<tenant>.sb.internal`) and, while the tailnet route is not yet approved, `AIFACTORY_CTL_JUMP=<ssh alias of the Proxmox host>`. Run `claude mcp reset-project-choices` once to approve the new server.

`console/bin/mcp` exposes the same reads and writes as MCP tools. It is registered in `.mcp.json` at the repository root, so opening Claude Code in this repository asks for approval once and then offers the tools as `mcp__aifactory__*`.

```bash
claude mcp list                    # aifactory appears (project scope)
claude mcp reset-project-choices   # approve again
```

| Tool | What it does |
|---|---|
| `overview` / `ticket_list` / `ticket_show` | Overview (`pj` narrows the run lists), list, one ticket (body, history, runs, jobs, the `attachments` listing; plus `sync_preview` when the ticket has a run) |
| `ticket_new` / `intake` | File a ticket (well-formed body / free text; intake is a job) |
| `ticket_attach` / `ticket_detach` | Add one attachment (pass the bytes in `content_base64`, or point at a file on the control host with `path` — one or the other) / remove one. `path` may only point under your home directory or `/tmp`, and may not contain a name starting with `.` (so config and key directories stay out of reach) |
| `ticket_action` | start / review / done / reopen / block (`done` and `set` with `pr` also transcribe the same thing as `run_action` onto the linked run when it is still waiting on a human) / set (an empty `note` clears it) / append (append to the end of the body; `text` required, `section` optional) / sync (`sync` defaults to `dry_run: true` and only returns the before/after; it writes only when you pass `dry_run: false`) |
| `ticket_run` / `dispatch` | kb run (lends a VM and goes to a PR; `dry_run` available) / run todos in order. Both are jobs |
| `run_list` / `run_show` / `read_file` | Run records and files under the allowed roots (`agent-*.log`, ticket attachments and so on). Images come back as an image block, so you can see them (up to 4 MiB; open anything larger from the console) |
| `run_action` | Record that a human closed a run out — opened a PR from the wip branch and merged it, or gave up (`kb run-note`). `close` records the outcome (`done` / `abandoned`) and the PR number; `note` rewrites the text |
| `sandbox_status` / `sandbox_ls` / `sandbox_release` | Lending state (`leases[]` carries task, VM name, IP, since and power state) / live list (job) / release (job) |
| `job_list` / `job_show` / `job_wait` / `job_stop` | Job list, output, wait (60 s by default, 300 s at most), stop |
| `logs` / `config` | intake / dispatch logs / workflows, routes, projects, git |

`tools/list` returns `annotations` for every tool (`title` and `readOnlyHint`; `destructiveHint` for release and stop). Without them Claude Code treats a tool as "not safe to call in parallel" and serialises the calls in one turn, so you wait even though the server is asynchronous (ADR-0038).

### How to drive a run

1. `ticket_run(id)` returns a job (`kb run` takes 5 to 80 minutes).
2. Watch it with `job_show(id, tail=2000)` or `run_show(name)` every few tens of seconds. `job_wait` waits 60 s by default and 300 s at most, and returns the job still running if it has not finished, so call it again. Other tools stay responsive while it waits (ADR-0028), but a client that ignores annotations serialises the calls on its side. If it looks stuck, use a shorter `timeout_s` or poll with `job_show`.
3. When it is over, read `outcome` from `run_show(name)` and `sync_preview` from `ticket_show(id)`, then go deeper with `read_file(path)` into `agent-*.log` / `code-*.log` / `work/*.md`. If a gate was red, `work/gates/<gate>.log` holds what it printed (error lines and the tail), so you do not have to ssh into the VM to find out why.
4. For free VMs, call `sandbox_status`. When the `sandbox ls` values are older than 600 s it starts a refresh job in the background and returns the old values with `ls_refreshing: true` and `ls_refresh_job` (the next call carries a fresh `pool_actual` / `free`; if it cannot start one, `ls_refresh_error` says why). The task, VM name, IP, lease start and power state of every lent VM are in `leases[]`, so you no longer have to read `state.json` over ssh.

Resources: `aifactory://board` (the board), `aifactory://ledger` (the ledger) and `aifactory://ticket/<id>` (a ticket body).

The console and the MCP server are separate processes, but the reads and writes live once in `console/lib/core.py` and the job records are shared. A run an AI starts over MCP shows up in the browser's job list, and the other way round. The decision record is ADR-0015.

## Tests

```bash
python3 -m unittest discover -s console/tests -v     # API and job management
python3 -m unittest discover -s workflow/tests -v    # the runner's streamed logs
```

The tests copy the ledger into a temporary directory (`KB_ROOT` / `CONSOLE_JOBS`), so they never touch the real database or job records. CI (`.github/workflows/ci.yml`) runs the same tests.

The decision record is ADR-0013 ([Decisions](../decisions/index.md)).
