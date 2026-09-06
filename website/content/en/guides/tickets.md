# Create tickets

What this page tells you: the shape of a ticket, filing from free text (intake), filing from a well-formed body (kb new), and how to correct a ticket afterwards.

## The shape of a ticket

A ticket is a single Markdown file. The runner reads it as is, so the shape is fixed.

```markdown
# fix: bring seeds and dev-entrypoint in line with the current models so db:seed passes
pr: 298                                  ← only for merge-pr: the target PR number

## Background
db/seeds.rb fails while baking the sandbox template. …

## Tasks
- Attach a team where db/seeds.rb calls Company.create!
- …

## Completion criteria
- bin/rails db:seed completes without errors on a clean database
- bundle exec rspec is fully green
- A PR exists
```

| Element | Rule |
|---|---|
| Line 1 | `# ` + title. The runner uses it for the branch name (`sandbox/<id>-<wf>-<slug>`) and the PR title. A prefix (`fix:` / `docs:` / `feat:` / `test:`) helps |
| Line 2 `pr: N` | Only for merge-pr. The target PR number |
| Body | Intent and background. Mark anything you guessed with "(assumed)" |
| `## Completion criteria` | In a form a machine or a person can check: "tests green", "a PR exists", "summary.md states X" |

The project and the kind (workflow) are not written in the body. kanban holds them as `pj` / `kind`.

## Filing from free text (intake)

A voice transcript, a Slack paste, a bullet list, anything works.

```bash
glue/bin/intake memo.txt            # from a file
cat memo.txt | glue/bin/intake -    # from standard input
```

intake does the following in a single LLM call (judgment class = Fable).

1. Decide the project and the kind
2. Write a title (50 characters or fewer, with a prefix)
3. Shape the body and add "Completion criteria"

The result is passed to `kb new` and an id is returned. The body ends with `(intake <time> / model <model> / confidence 0.9 / <reason>)`, so you can later see how the LLM decided.

### Looking at the decision first

```bash
glue/bin/intake memo.txt --dry-run
```

Prints only the decision JSON (pj / kind / title / body / confidence / reason) without filing.

### When you already know the project or kind

There is no reason to let the LLM guess what you know. Three deterministic ways exist, in order of precedence: `--pj` / `--kind` > header lines in the text > the LLM.

```bash
glue/bin/intake memo.txt --pj kumitate --kind bug           # options
printf 'pj: kumitate\nkind: chore\nrequest…' | glue/bin/intake -   # pj: / kind: lines within the first 5 lines
```

What you specify overrides the LLM's decision; the LLM only shapes the title and completion criteria.

### If the decision was wrong

Fix it afterwards with `kb set`. The runner reads `pr:` from the body, so changing `--pr` also rewrites that line.

```bash
kanban/bin/kb set 204 --kind feature
kanban/bin/kb set 204 --pr 300
```

If it keeps making the same mistake, adjust the decision guidance (the prompt text) inside `glue/bin/intake`.

## Filing from a well-formed body (kb new)

If you can write the body in the ticket shape yourself, skip intake and file directly. No LLM is called.

```bash
kanban/bin/kb new <pj> <kind> "<title>" --body ticket.md
kanban/bin/kb new <pj> <kind> "<title>" --body - <<'EOF'
Body…
## Completion criteria
- …
EOF
kanban/bin/kb new kumitate merge-pr "Merge PR #298 into develop" --pr 298 --body ticket.md
```

- `pj` must be a project with a definition directory (`$AIFACTORY_WORKSPACE/projects/<pj>/`, or `examples/projects/<pj>/` as a fallback); `kind` must be a workflow with `workflow/kit/workflows/<kind>.yml`. Both are validated
- The id is sequential (`MAX(id)+1`, minimum 100). It has at least 3 digits so it can be used in the DNS name `task-<id>.sb.internal`
- `--id N` sets a specific number (for importing past records)
- `--note` adds a note, `--status` sets the initial state (default todo)

## Writing a good ticket

| Good | Bad |
|---|---|
| "Two calendar title tests depend on the current time because of August-fixed fixtures. Fix them with a frozen time" | "Tests fail, fix them" |
| Completion criteria: "rspec fully green", "do not delete or skip existing tests" | No completion criteria |
| State the scope: "seeds and entrypoint only. Do not change the models" | No scope (the agent fixes things outside the scope) |
| Say whether production is affected | Nothing (the planner writes STOP and halts) |

Agents work under the rule "if you find a problem outside the scope, report it instead of fixing it" (`workflow/kit/roles/_common.md`). The more precisely you state the scope, the smaller and more readable the diff.

## Listing and checking

```bash
kanban/bin/kb list                 # everything except done
kanban/bin/kb list --all --pj kumitate
kanban/bin/kb show 204             # metadata + body
```

`workspace/kanban/BOARD.md` is regenerated whenever state changes. In a browser, the board of the [Web console](console.md) shows the same thing.
