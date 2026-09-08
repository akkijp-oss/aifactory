# Run work

What this page tells you: the three ways to run a ticket (`kb run` / `dispatch` / calling the runner directly), dry runs, what you can see while it runs, and how to stop and resume.

## Three entry points

```mermaid
flowchart LR
  D[glue/bin/dispatch<br>todo items in order] --> K[kanban/bin/kb run id<br>one ticket, updates state]
  K --> R[workflow/bin/run<br>the runner itself]
  T[By hand] --> R
```

| Entry point | When | State updates |
|---|---|---|
| `glue/bin/dispatch` | Run todo items unattended, in order. The everyday choice | Automatic via kb |
| `kanban/bin/kb run <id>` | Run a single ticket. Check definitions with a dry run | Automatic |
| `workflow/bin/run …` | Experiments that bypass kanban. Developing workflow definitions | None. Use `kb sync` afterwards |

## Running in bulk with dispatch

```bash
glue/bin/dispatch --once                 # the oldest todo, one ticket
glue/bin/dispatch --max 3                # up to 3
glue/bin/dispatch --pj kumitate          # restrict to a project
glue/bin/dispatch --dry-run              # assemble prompts only, no VM
glue/bin/dispatch --wait 60              # do not skip a full pool: wait up to 60 minutes for a free VM
```

dispatch makes no decisions. It checks only two things.

- The project has **no** `project.yml` (in `$AIFACTORY_WORKSPACE/projects/<pj>/`, or `examples/projects/<pj>/` as a fallback) → mark `blocked` (with the reason in the note) and move on
- **All** of the project's pool (3 VMs) is lent out → skip the project and look for the next project's todo

`--wait` drops the second check and lets `kb run --wait <minutes>` wait for a free VM instead. When you want to push more tickets through than the pool holds, nobody has to watch for runs to finish and start the next one by hand.

It is sequential. The next ticket does not start until the current one finishes. A run whose gates take 60 minutes makes the others wait.

## Running one ticket with kb run

```bash
kanban/bin/kb run 204                    # with the workflow matching the kind
kanban/bin/kb run 204 --workflow chore   # run once with a different workflow (the kind is left alone)
kanban/bin/kb run 204 --dry-run          # validate definitions and assemble prompts only
kanban/bin/kb run 204 --keep             # do not release the VM afterwards (to look inside)
kanban/bin/kb run 204 --resume           # continue from the next step in state.json on the VM already lent
kanban/bin/kb run 204 --wait             # wait for a free VM when the pool is full (60 minutes; `--wait 30` for 30)
```

`kb run` sets the state to `in_progress`, calls the runner, then reads `state.json` when it finishes and advances the state.

| Runner result | kb state | Note |
|---|---|---|
| `pr_url` contains MERGED | `done` | Merged |
| `pr_url` present | `review` | A human reviews and merges |
| `end` without a PR (research etc.) | `done` | Finished without a PR |
| `human` without a PR | `blocked` | Handed to a human. Work is preserved on `origin/sandbox/<id>-<wf>-wip` |
| The runner crashed | `blocked` | Exit code and the next step |
| `--wait` ran out with no free VM | `todo` | Nothing to fix, so it goes back to todo and can be run again later (ADR-0031) |

While `--wait` waits, the ticket stays `in_progress`, and the console board and run record show "waiting for a free VM" with the elapsed time.

## What you can see while it runs

The terminal prints `[run <pj>/<id> <elapsed s>] <step>: PASS/FAIL → <next>` per step. At the same time files accumulate in `$AIFACTORY_WORKSPACE/runs/<date>-<pj>-<id>/` (default `workspace/runs/`).

| File | When | What |
|---|---|---|
| `ticket.md` | At start | The ticket handed over |
| `state.json` | Every step | Where it is, loop counts, the result |
| `prompt-<step>-<n>.md` | Just before an agent step | The assembled prompt (8 layers) |
| `agent-<step>-<n>.log` | During an agent step (streamed) | The `claude -p` event stream in readable form (timestamps, tool calls ▶, the head of each result ↳, and the final result with cost) |
| `agent-<step>-<n>.jsonl` | During an agent step (streamed) | The same events as raw JSON (for debugging) |
| `code-<step>-<n>.log` | During a code step (streamed) | Output of gates / pr |
| `work/` | On release | Artifacts collected from the VM |

`current` in `state.json` names the step that is running and its log file, so you can `tail -f` a log in the middle of a step. In a browser, the run screen of the [Web console](console.md) opens the same log automatically.

You can also look inside the VM from another terminal.

```bash
sandbox ls                                   # which VM is lent
sandbox ssh 204                              # log in (user dev)
sandbox ssh 204 'cd $SANDBOX_APP_DIR && git log --oneline -5'
sandbox url 204                              # the app URL (opens in a browser)
```

## Stopping and redoing

- **Stop**: Ctrl-C the runner process. The VM stays lent, so either return it with `sandbox release <id>` or continue with `--resume`
- **Failed for VM reasons** (ssh dropped, token expired, and so on): `kb reopen <id>` → `kb run <id>`. A rerun on the same day moves the previous `runs/` directory to `-attemptN` first
- **The agent's output was poor and the run went to `human`**: read `work/` and `agent-*.log`, fix the ticket, then `kb reopen` → `kb run`. The work is on `origin/sandbox/<id>-<wf>-wip` if you want to keep it
- **You changed a definition** (project.yml / workflow yml / roles): it does not affect a running run. It applies from the next run

## Calling the runner directly

```bash
workflow/bin/run <pj> <task-id> <workflow> <ticket.md> [--dry-run] [--keep] [--resume]
workflow/bin/run kumitate 900 hotfix ticket.md --dry-run
```

Because kanban is bypassed, the state does not change. Afterwards run `kb sync <id> --run <run directory name>` (for example `2026-09-06-kumitate-206`) to catch up. Hand-assigned task ids collide with kanban's numbering, so use this only for experiments.
