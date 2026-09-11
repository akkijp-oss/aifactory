# Roles and models

`workflow/kit/roles/*.md` (role constitutions) and `workflow/kit/routes.env` (class → model).

## Roles

| Role | Class | Does | Does not | Output |
|---|---|---|---|---|
| **planner** | judgment | Reads the ticket and repository; decides reproduction, hypotheses, scope (per file), verification and risks. Writes **STOP** at the top if the request is unclear, contradictory or dangerous | Write code | `plan.md` |
| **implementer** | coding | Implements according to the plan. For bugs, writes a failing test first and confirms red. Runs lint / types / relevant tests to green before committing | Step outside the scope (stops and explains in report.md). Push | git commits + `report.md` |
| **researcher** | research | Splits the question into at most 3 items, gathers primary sources from the repository and the web, organises them with citations. GitHub via `gh` (CI history via `gh run list`) | Change code, commit. Write guesses as facts | `research.md` (in the research workflow the judge writes `summary.md`) |
| **reviewer** | judgment | Reads diff, plan, report and gate results in the order "scope → correctness → safety → project-specific → gates" and decides PASS / FAIL, and writes how heavy a FAIL is (`severity: minor` / `major`). Concerns outside the scope go into notes for humans | Fix code | `review.md` |

Every role is preceded by `_common.md` (shared rules).

## Shared rules (`_common.md`)

- Work on the work branch. No direct commits to `main` / `develop`, no branch switching
- **Do not push.** Push and PR are done by code (the runner)
- Commit yourself. Messages in Japanese, "what and why" on line 1
- Do not `git add` untracked files. Never `git add -A`
- Do not write secrets to files or logs
- Do not change anything outside the scope. Report problems outside the scope instead of fixing them
- When instructions and reality disagree, treat reality as truth and report the discrepancy
- Do not fill gaps with guesses. When a judgement is needed, write the options and a recommendation and proceed on the safe side
- Always write the required artifacts to the specified path under `~/work/<id>/` (missing means the step fails)

## Output shapes

=== "plan.md"

    ```
    # Plan: <title>
    ## Reproduction and hypotheses (with confidence)
    ## Scope (per file, including what is left alone)
    ## Verification
    ## Risks and decision rules
    ```
    `STOP` at the top with reasons and questions for the human if dangerous.

=== "report.md"

    ```
    # Report: <title>
    ## What changed
    ## Tests run and results
    ## Found outside the scope (not fixed)
    ## Judgement calls
    ```

=== "research.md"

    ```
    # Research: <question>
    ## Conclusion (3 lines, facts only)
    ## Findings per item (with sources)
    ## Unknown / to confirm
    ## Notes for the planner
    ```

=== "review.md"

    ```
    # レビュー: PASS | FAIL
    severity: minor | major   (on FAIL only, on the second line)
    ## Summary (3 lines at most)
    ## Findings (on FAIL; numbered, file:line, how to fix)
    ## Questions for humans (if any)
    ## What was checked (scope / correctness / safety / project points / gates)
    ```

    The first line is always `# レビュー: PASS` or `# レビュー: FAIL` (the runner branches on it). `severity` goes on its own line below, never mixed into the first line. A FAIL marked `severity: minor` (findings small enough for the implementer to fix in one more loop) sends the run back to implement **one more time** even when the loop limit is used up (ADR-0053).

## routes.env

```
MODEL_judgment=claude-fable-5-1
MODEL_research=claude-sonnet-5
MODEL_coding=claude-opus-5
MODEL_default=claude-opus-5
```

| Class | Use | Default roles |
|---|---|---|
| judgment | Critical judgement | planner, reviewer, the judge (research workflow), intake |
| research | Web research | researcher |
| coding | Coding | implementer |

"Judgement on Fable, web research on Sonnet, everything else on Opus" is the maintainer's decision (2026-09-06). Edit `routes.env` to use other models.

## Overrides

| Scope | Method |
|---|---|
| Everything | Edit `routes.env` |
| One step (by class) | `model_class: judgment` etc. in the workflow yml |
| One step (pin a model name) | `model: claude-opus-5` in the workflow yml. Other steps of the same class stay put |
| One run | Environment variable `CLAUDE_MODEL=claude-opus-5 kb run 204` |

Weakest to strongest: the role's default class → the step's `model_class` → the step's `model` → `CLAUDE_MODEL`.

## Changing it from the console

Console → Settings → workflow → step (agent steps only) has "change the model". Pick one of the three scopes above
(this step's model / this step's class / the shared route), then press "check the change". Before anything is written you see:

- The effective model before and after, and **every step whose effective model changes** (for a shared route, that includes
  steps in other workflows)
- The runs that are in flight (a running run keeps the settings it read at startup; nothing switches mid-run)
- The file being written and its `git status` line

Saving keeps the previous content next to the file as `.bak-<timestamp>` and appends a line to `logs/config-changes.jsonl`
(also shown as "recent changes" on the page). To roll back, put the backup's content back.

!!! warning "The console does not commit"
    The files it writes (`workflow/kit/routes.env` and the workflow yml) are part of the repository. The console only writes
    the work tree, so a `git pull` on the control host can undo the change. Commit it to make it permanent (ADR-0065).

!!! note "Which model names are accepted"
    The name must reveal its key family (fable / opus / sonnet / haiku). Without a family the runner does not pick a
    per-family key and falls back to the shared `CLAUDE_CODE_OAUTH_TOKEN`; the step still runs, but not necessarily on the
    key you meant, so the console refuses the value (editing the file directly still accepts it). Suggestions come from the
    values currently in use, and new names can be typed in directly.

## Adding a role

1. Write `workflow/kit/roles/<role>.md` (class, duties, prohibitions, output shape)
2. Add the role → default class mapping in the runner (`model_for` in `bin/run`)
3. Use it as `role: <role>` in a workflow step
