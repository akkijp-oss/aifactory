# kb (kanban CLI)

`kanban/bin/kb`. The ticket ledger (SQLite) holding ids, state and history, and the entry point for calling the runner. Python 3 standard library only.

```
kb new <pj> <kind> <title> [--body FILE|-] [--pr N] [--id N] [--status S] [--note TEXT]
kb list [--status S] [--pj P] [--all]
kb show <id>
kb start|review|done|reopen <id> [--note TEXT]
kb block <id> --note TEXT
kb set <id> [--status S] [--pr N] [--run DIR] [--note TEXT] [--kind K]
kb append <id> [--section S] [--text T]
kb next [--pj P] [--json]
kb run <id> [--workflow W] [--dry-run] [--keep] [--resume] [--wait [minutes]]
kb sync <id> [--run DIR]
kb history <id>
kb render
```

## Locations

| Item | Path |
|---|---|
| Database (source of truth) | `$AIFACTORY_WORKSPACE/kanban/kanban.db` (default `<repo>/workspace/kanban/`, not tracked by git) |
| Bodies (source of truth) | `kanban/tickets/<id>-<pj>-<slug>.md` under the same root |
| Board (generated) | `kanban/BOARD.md` under the same root |
| Relocating | Environment variable `AIFACTORY_WORKSPACE=<dir>` moves the whole workspace; `KB_ROOT=<dir>` moves only the database, tickets and BOARD (for tests) |

## States

| State | Label | Meaning |
|---|---|---|
| `todo` | not started | Filed |
| `in_progress` | running | The runner is executing |
| `review` | awaiting review | A PR exists. Waiting for a human |
| `blocked` | waiting for a human | Went to human, crashed, or no project.yml. The note says why |
| `done` | done | Merged, or finished without a PR |

## Commands

### new

```bash
kb new <pj> <kind> "<title>" [--body FILE|-] [--pr N] [--id N] [--status S] [--note TEXT]
```

| Argument | Meaning |
|---|---|
| `pj` | A project definition directory must exist (`$AIFACTORY_WORKSPACE/projects/<pj>/`, else `examples/projects/<pj>/`) |
| `kind` | Must match `workflow/kit/workflows/<kind>.yml` (chore / bug / feature / hotfix / research / merge-pr) |
| `title` | Line 1. Truncated at 70 characters |
| `--body` | Body file. `-` for standard input. No body if omitted |
| `--pr` | Target PR number for merge-pr. Written as `pr: N` on line 2 of the body |
| `--id` | Explicit number (default `MAX(id)+1`, minimum 100). Error if it exists |
| `--status` | Initial state (default todo) |
| `--note` | Note |

Output: one line `<id> <state> <pj> <kind> <PR> <title>` plus the body path. The file slug comes from the ASCII part of the title, or the kind if there is none.

### list / show / next

```bash
kb list                          # everything except done
kb list --status review          # by state
kb list --pj kumitate --all      # by project; --all includes done
kb show 204                      # all fields + body
kb next                          # the oldest todo
kb next --pj kumitate --json     # JSON (for dispatch and external tools; path holds the body's absolute path)
```

### Advancing state

```bash
kb start 204                     # → in_progress
kb review 204                    # → review
kb done 204 --note "merged"      # → done
kb block 204 --note "waiting for a production-impact decision"   # → blocked (--note required)
kb reopen 204                    # → todo
kb set 204 --status review --pr 300 --run 2026-09-06-kumitate-204 --note "…" --kind feature
```

Changing `--pr` also rewrites the `pr:` line in the body (the runner reads it from there). `--run` is the run directory name (relative to `workspace/runs/`). `--kind` is validated. Everything is recorded in the history and BOARD.md is regenerated.

`kb set 204 --note ''` clears the note (NULL in the DB). A field you do not pass is left alone. Over MCP and the HTTP API (`console`), `note` is treated as "present as an empty string = clear it, key absent = leave it alone"; an empty string used to be ignored as "not given". `status` / `kind` / `pr` still ignore an empty string as "not given".

### append

```bash
kb append 204 --section "PM 補足" --text "the `._*` files in 218 are AppleDouble"
kb append 204 --section "PM 補足" < memo.md      # without --text, read from stdin
```

Appends to the **end** of the ticket body. With `--section`, a `## <heading>` line is written first. Empty text, or a ticket whose body file is missing, is an error (exit code 1).

The insertion point is fixed at the end. It does not go before `## 完了条件` because `kb` has no way to recognise sections, and appending at the end keeps the change in one place. The heading is what tells you a passage was added later.

The text itself lives in the body (the file is the source of truth). The history only records when and how much was added, as `body  - → append 24字 (PM 補足)` — `history` has three columns (`field` / `old` / `new`) and does not keep diffs.

### run

```bash
kb run 204 [--workflow W] [--dry-run] [--keep] [--resume] [--wait [minutes]]
```

1. Error if the project has no `project.yml`. `done` tickets error except with `--dry-run` (`reopen` first)
2. Sets `in_progress` and records the run directory name (`<date>-<pj>-<id>`; the directory lives under `workspace/runs/`)
3. Calls `workflow/bin/run <pj> <id> <workflow> <body path> [flags]`. `--workflow` only changes how this run is executed; `kind` is left alone (the history records `workflow → <name>` and `runs/<run>/state.json` holds the authoritative value; ADR-0030). A `--resume` without `--workflow` restarts with the `workflow` from that `state.json`
4. Afterwards reads `state.json` and advances the state (table below)

With `--wait`, a run whose project pool is full does not fail: it waits for a free VM and then starts (minutes; 60 when the value is omitted). While waiting the ticket stays `in_progress`, and the console board and run page show "waiting for a free VM" with the elapsed time. Only when the limit is exceeded does the ticket go back to `todo`, with the reason in its note (ADR-0031).

| state.json | State | Note |
|---|---|---|
| `pr_url` contains `MERGED` | done | Merged URL |
| `pr_url` present | review | PR URL |
| `result: end`, no PR | done | Finished without a PR (research etc.) |
| `result: human`, no PR | blocked | Handed to a human (wip branch) |
| `result: failed` | blocked | Could not take a VM, so no step ran (the last line of `error` goes into the note) |
| `result: failed` with `failure: wait_timeout` | todo | `--wait` ran out before a VM came free. There is nothing to fix, so the ticket goes back to todo |
| No `finished`, runner exited non-zero | blocked | Runner exited without a record, rc=N |

`--dry-run` leaves the state unchanged. The exit code is the runner's (0 = end or PR present, 2 = human).

### sync

```bash
kb sync 204 [--run DIR]
```

Re-reads `state.json` and aligns the state. Use it after calling the runner directly, when `kb run` died midway, or to import a run done by another session. If `finished` is missing, the state stays `in_progress` and the note records the next step.

### history / render

```bash
kb history 204                   # time / field / old → new
kb render                        # regenerate BOARD.md (normally automatic)
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Argument or existence error (`[kb] error: …` on stderr) |
| The runner's code | `kb run` returns the runner's exit code as is |

## Implementation notes

- Tables: `tickets` (id, pj, kind, title, status, file, pr, run, note, created, updated) and `history` (ticket, at, field, old, new)
- Single-Mac assumption. Writing the same database from several machines will conflict
- Makes no decisions. Calls no LLM
