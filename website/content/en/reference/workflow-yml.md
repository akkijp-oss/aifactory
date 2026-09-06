# workflow yml

`workflow/kit/workflows/<name>.yml`. The procedure for one ticket kind. It holds only the step sequence and branching; prose (role constitutions) lives in `roles/*.md`. Validated against `workflow/kit/schema/workflow.schema.json`.

## Top level

| Field | Type | Required | Meaning |
|---|---|---|---|
| `name` | string (`^[a-z][a-z0-9-]*$`) | ✅ | Workflow name. Used as `kind` in `kb new` |
| `description` | string | ✅ | One-line description. intake shows it in the kind list |
| `base_branch` | string | | Source of the work branch and PR target. Defaults to `base_branch` in `project.yml`. Writing `hotfix_base` uses the project's `hotfix_base` |
| `inputs` | array | | Input artifact names for the whole workflow. v1 is fixed at `[ticket.md]` |
| `start` | `base` / `pr` | | How the work branch is created. `base` = new branch from base (default). `pr` = check out the head of the PR given by `pr: N` on line 2 of the ticket (base is that PR's base) |
| `steps` | array | ✅ | The step sequence (at least one) |

## step

| Field | Type | Meaning |
|---|---|---|
| `id` | string (`^[a-z][a-z0-9_-]*$`) | Step name. Used as a transition target. Required |
| `role` | planner / implementer / reviewer / researcher | Agent step. Constitution in `roles/<role>.md`, model from the role's default class |
| `code` | string | Code step. Path relative to `kit/steps/`. Runs on the Mac and enters the VM with `sandbox ssh` |
| `brief` | string (Markdown) | Extra instruction for this step, appended after the role constitution |
| `model_class` | judgment / research / coding | Overrides the role's default class |
| `inputs` | array of artifact | Read from `~/work/<id>/` in the VM and attached to the prompt. The reviewer also gets the diff automatically |
| `outputs` | array of artifact | Artifacts the step must produce. Agent steps are told "write here". Missing means failure |
| `next` | transition | Unconditional next step. A step without branching that fails goes to human |
| `on_pass` / `on_fail` | transition | Branch on pass / fail |
| `timeout_min` | integer | Agent time limit (default 60) |

Exactly one of `role` and `code` (both is a schema error).

### artifact

A path relative to `~/work/<id>/` (`plan.md`, `gates.txt`). Two special values.

| Value | Meaning |
|---|---|
| `git` | In outputs: "commit to the work branch". Not collected as a file |
| `pr_url` | A code step writes the PR URL to `~/work/<id>/pr_url`. The runner copies it to `pr_url` in `state.json` |

### transition

Either a string or an object.

| Form | Meaning |
|---|---|
| `"implement"` | Go to that step |
| `"end"` | Finish successfully |
| `"human"` | Hand over to a human and stop. Work is preserved on the wip branch |
| `{ goto: implement, max_loops: 2, else: human }` | Go back to `implement`. This transition may loop 2 times; beyond that, `human` |

`max_loops` defaults to 1, `else` to `human`. Counts are kept in `loops` of `state.json` as `"<from>-><to>": n`.

## Examples

=== "bug.yml"

    ```yaml
    name: bug
    description: Bug fix. plan → reproduction test → fix → gates → review → PR
    inputs: [ticket.md]
    steps:
      - id: plan
        role: planner
        brief: |
          Pin down the reproduction first. Include in the plan where (which test file) the reproduction test goes.
        outputs: [plan.md]
        next: implement
      - id: implement
        role: implementer
        brief: |
          **Write the reproduction test first and confirm it is red** before fixing. If no reproduction test is possible, explain why in report.md and show an alternative verification.
        inputs: [plan.md]
        outputs: [git, report.md]
        next: gates
      - id: gates
        code: gates.sh
        outputs: [gates.txt]
        on_pass: review
        on_fail: { goto: implement, max_loops: 2, else: human }
      - id: review
        role: reviewer
        inputs: [plan.md, report.md, gates.txt]
        outputs: [review.md]
        on_pass: pr
        on_fail: { goto: implement, max_loops: 1, else: human }
      - id: pr
        code: pr-create.sh
        inputs: [plan.md, report.md, review.md, gates.txt]
        outputs: [pr_url]
        next: human
    ```

=== "research.yml"

    ```yaml
    name: research
    description: Research. The researcher gathers primary sources into research.md; the planner judges the key points and next step into summary.md
    inputs: [ticket.md]
    steps:
      - id: research
        role: researcher
        outputs: [research.md]
        next: judge
      - id: judge
        role: planner
        brief: |
          Read research.md and write the answer to the question and "what to do next (including deciding not to)" into summary.md.
          No code, no plan. Short enough for a human to read in 2 minutes.
        inputs: [research.md]
        outputs: [summary.md]
        next: end
    ```

=== "merge-pr.yml (essentials)"

    ```yaml
    name: merge-pr
    description: Resolve conflicts on an existing PR, pass gates and review, merge into base
    start: pr                       # check out the head of the ticket's pr: N
    steps:
      - id: resolve
        role: implementer
        outputs: [git, report.md]
        next: gates
      - id: gates
        code: gates.sh
        on_pass: review
        on_fail: { goto: resolve, max_loops: 2, else: human }
      - id: review
        role: reviewer
        on_pass: merge
        on_fail: { goto: resolve, max_loops: 1, else: human }
      - id: merge
        code: pr-merge.sh
        on_pass: end
        on_fail: human
    ```

## Writing notes

- Quote values containing backticks or `: ` with `"..."` (PyYAML misreads them). Write `brief` as a `|` block
- Nothing project-specific (that goes in `project.yml`)
- A new role needs `roles/<role>.md` and a default-class mapping in the runner
- Changes take effect from the next run, never a running one
- `kb run <id> --dry-run` checks the schema and assembles the prompts
