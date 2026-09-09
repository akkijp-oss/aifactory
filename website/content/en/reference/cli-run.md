# run (workflow runner)

`workflow/bin/run`. The runner (v1, Python 3, `pyyaml` + `jsonschema`) that reads a workflow definition and executes its steps in order inside a sandbox VM. Normally invoked through `kb run`.

```
workflow/bin/run <pj> <task-id> <workflow> <ticket.md> [--dry-run] [--keep] [--resume] [--from[=step]] [--branch=name] [--wait[=seconds]]
```

| Argument | Meaning |
|---|---|
| `pj` | A project with a `project.yml` (`$AIFACTORY_WORKSPACE/projects/<pj>/`, else `examples/projects/<pj>/`) |
| `task-id` | The id passed to `sandbox take` (3 or more digits, assigned by kanban) |
| `workflow` | A name in `workflow/kit/workflows/` |
| `ticket.md` | The ticket. Line 1 is the title, optionally `pr: N` on line 2 |
| `--dry-run` | No VM: validate definitions and assemble prompts only, into `workspace/runs/…-dry/` |
| `--keep` | Do not release the VM afterwards (to look inside) |
| `--resume` | Continue from the next step in `state.json` on the VM already lent |
| `--from[=step]` | Redo a run that ended at `human` from the given step **on a new VM**. Without a step, uses the previous `resume_step`. The previous run is passed in the `AIFACTORY_FROM_RUN` environment variable (run names only; ADR-0034) |
| `--branch=name` | The branch to continue from with `--from`. Defaults to the previous `wip_branch` |
| `--wait[=seconds]` | When the pool has no free VM, wait for one and retry `sandbox take` (3600 seconds on its own; the retry interval is `AIFACTORY_WAIT_POLL_S` seconds, 30 by default) |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | `result: end`, or a PR was created |
| 1 | Definition error, argument error, missing step |
| 2 | `result: human` (no PR) |

## Behaviour

1. Reads `kit/workflows/<wf>.yml` and the project's `project.yml`, validates with `kit/schema/`
2. Decides the base branch (`base_branch: hotfix_base` in the workflow → `hotfix_base` in the project; `workflow_overrides` overrides)
3. For merge-pr (`pr: N` in the body) gets head / base with `gh pr view` and uses the head as the work branch. Otherwise `sandbox/<id>-<wf>-<slug>`
4. Creates `workspace/runs/<date>-<pj>-<id>/`. If it exists and `--resume` is not given, moves the previous one to `-attemptN`. Writes `ticket.md` and `state.json`
5. `sandbox take <pj> <id>`. Fetches base in the VM and creates the work branch (with `--from`, branches from `origin/<wip branch>` instead of base and copies the previous run's `work/*.md` onto the VM). Places the ticket at `~/work/<id>/ticket.md`. With `--wait`, a failure that says the pool is full sets `current` to `wait-vm` and retries the take until one comes free (ADR-0031)
6. Executes steps in order (below) until `end` or `human`
7. On `human`, pushes to `origin/sandbox/<id>-<wf>-wip` to preserve the work
8. Collects `~/work/<id>/` into `workspace/runs/…/work/`. `sandbox release` unless `--keep`
9. Writes `result` / `pr_url` / `wip_branch` / `finished` / `elapsed_s` to `state.json`. A run that stopped at `human` also gets `resume_step` (the step to redo), and a run started with `--from` gets `resumed_from` / `from_step` / `from_branch`

When `--wait` runs out, the record carries `failure: "wait_timeout"` and `waited_s` (the seconds waited) beside `result: failed`, and the exit code is 2. `kb` reads that marker and puts the ticket back to `todo` instead of `blocked`.

### Agent steps

- Assembles the 8-layer prompt, keeps it as `workspace/runs/…/prompt-<step>-<n>.md`, places it at `/home/dev/prompt.md` in the VM
- Model: the step's `model_class` → the role's default class → `routes.env`. The environment variable `CLAUDE_MODEL` wins over all
- Runs `cd $SANDBOX_APP_DIR && timeout <timeout_min>m claude -p "$(cat /home/dev/prompt.md)" --model <model> --output-format stream-json --verbose`. The runner reads the events line by line and streams a readable form to `agent-<step>-<n>.log` (timestamps, tool calls ▶, the first 3 lines of each result ↳, the final result with cost). The raw JSON goes to `agent-<step>-<n>.jsonl`
- While a step runs, `current` in `state.json` holds `{step, kind, log, since}` (`null` once the step ends)
- Pass if every file in `outputs` (except `git` / `pr_url`) exists in `~/work/<id>/`
- Past the time limit (the step's `timeout_min`, 60 minutes by default) `timeout` kills the step with exit code 124. The runner keeps this apart from an ordinary failure: it commits the uncommitted tracked changes as `wip: step timeout` (never adding untracked files) and only then fails the step. When the run lands on `human` the preserve branch `origin/sandbox/<id>-<wf>-wip` therefore carries the half-finished work, and the next run can pick it up
- The "Outputs (required)" section of the prompt states the step's limit in minutes and asks for a `wip:` commit every 30 minutes

### Code steps

- Runs `kit/steps/<script>` on the Mac. Output is streamed to `code-<step>-<n>.log` (`current` is set the same way)
- Env passed: `PJ` `TASK` `RUN_DIR` `PROJECT_DIR` `GATES` `WORK` `APP_DIR` `BASE` `BRANCH` `WORKFLOW` `TITLE` `PR_NUMBER` `KNOWN_RED`
- Reissues the GitHub App token before running (`sandbox reinject`)
- Pass on exit code 0

### Transitions

| Step definition | Result | Next |
|---|---|---|
| `next: X` | pass | X |
| `next: X` | fail | human (a step without branching that fails goes to a human) |
| `on_pass: X` / `on_fail: {goto: Y, max_loops: N, else: Z}` | pass | X |
| Same | fail, loops < N | Y (counted in `loops` of `state.json`) |
| Same | fail, loops ≥ N | Z |

On send-back the previous result is attached to the next prompt as "Previous result (fix this)". When sending from a code step (gates) back to an agent step, "report, do not fix, if it is already red on base" is stated too.

## state.json

```json
{
  "pj": "kumitate", "task": "204", "workflow": "bug",
  "branch": "sandbox/204-bug-fix-calendar-test", "base": "develop",
  "started": "2026-09-06T12:00:00",
  "history": [
    {"step": "plan", "ok": true, "next": "implement", "at": "…"},
    {"step": "gates", "ok": false, "next": "implement", "at": "…"}
  ],
  "loops": {"gates->implement": 1},
  "current": null,
  "next": "end",
  "result": "end",
  "error": "",
  "last_output": "",
  "pr_url": "https://github.com/akkijp/kumitate/pull/300",
  "wip_branch": "",
  "finished": "2026-09-06T12:31:00",
  "elapsed_s": 1860
}
```

`pr_url` ends with ` MERGED` when the merge step of merge-pr succeeded.

A run that stops at `human` records why in `error`, on one line. A step killed by its time limit reads `implement: 時間上限 60 分で中断（timeout）`, and its `history` entry carries `"failure": "timeout"` and `"timeout_min"`. The tail of the agent's standard output is kept out of `error` and stored in `last_output` (up to 3000 characters) — a timed-out step used to end up with a lint summary line as its `error`, which read as "lint failed". The web console and the MCP `run_show` use the marker to say the step was cut off at its time limit.

## Environment variables

| Variable | Meaning |
|---|---|
| `CLAUDE_MODEL` | One-off override of the model for every agent step |
| `MERGE_METHOD` | Merge method for merge-pr (merge / squash / rebase, default merge) |

## Traps encountered

| Trap | Handling |
|---|---|
| Sending an agent back to "fix" a gate already red on base makes it change things outside the scope | `known_red_gates` in `project.yml`. The runner downgrades FAIL → INFO |
| Auto-committing with `git add -A` picks up generated files | Agents are limited to `git add -u` by rule |
| A full-width bracket right after a variable (`$loop）`) makes bash treat it as an undefined variable | Always `${var}`. One reason the runner is Python |
| Editing a bash script while it runs breaks it | Do not touch scripts while a run is in progress |
| A same-day rerun overwrote `workspace/runs/` | Move the previous one to `-attemptN` first |
| The one-hour GitHub App token expired during a long run | Reissue before code steps |
| Malformed UTF-8 in a child process's output killed the runner | Read with `errors="replace"` |
