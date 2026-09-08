# kb (kanban CLI)

`kanban/bin/kb`. The ticket ledger (SQLite) holding ids, state and history, and the entry point for calling the runner. Python 3 standard library only.

```
kb new <pj> <kind> <title> [--body FILE|-] [--pr N] [--id N] [--status S] [--note TEXT]
kb list [--status S] [--pj P] [--all]
kb show <id>
kb start|review|done|reopen <id> [--note TEXT]
kb block <id> --note TEXT
kb set <id> [--status S] [--pr N] [--run DIR] [--note TEXT] [--kind K]
kb next [--pj P] [--json]
kb run <id> [--workflow W] [--dry-run] [--keep] [--resume]
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

### run

```bash
kb run 204 [--workflow W] [--dry-run] [--keep] [--resume]
```

1. Error if the project has no `project.yml`. `done` tickets error except with `--dry-run` (`reopen` first)
2. Sets `in_progress` and records the run directory name (`<date>-<pj>-<id>`; the directory lives under `workspace/runs/`)
3. Calls `workflow/bin/run <pj> <id> <workflow> <body path> [flags]`. Passing `--workflow` also updates `kind`
4. Afterwards reads `state.json` and advances the state (table below)

| state.json | State | Note |
|---|---|---|
| `pr_url` contains `MERGED` | done | Merged URL |
| `pr_url` present | review | PR URL |
| `result: end`, no PR | done | Finished without a PR (research etc.) |
| `result: human`, no PR | blocked | Handed to a human (wip branch) |
| `result: failed` | blocked | Could not take a VM, so no step ran (the last line of `error` goes into the note) |
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
