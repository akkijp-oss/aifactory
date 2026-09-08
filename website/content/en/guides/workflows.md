# Choose a workflow

What this page tells you: the flow of the six workflows, when to use each, which roles and models run, and the retry limits.

## Overview

| Workflow | Flow | When | Exit |
|---|---|---|---|
| **hotfix** | plan → implement → gates → review → sync → pr | Minimal fix for a production incident. Targets `hotfix_base` (usually `main`) | PR → human |
| **bug** | plan → implement (reproduction test first) → gates → review → sync → pr | Bug fixes | PR → human |
| **feature** | research → design → implement → gates → review → sync → pr | New functionality, with research and design steps | PR → human |
| **chore** | implement → gates → sync → pr | Docs, dependency updates, settings; no judgement needed. No plan, no review | PR → human |
| **research** | research → judge → end | Investigation only. No code changes. Collects `summary.md` | done (no PR) |
| **merge-pr** | resolve → gates → review → merge | Resolve conflicts on an existing PR and merge it. Target given by `pr: N` in the body | merged |

## Flow diagrams

```mermaid
flowchart LR
  subgraph feature
    r1[research<br>Sonnet] --> d1[design<br>Fable] --> i1[implement<br>Opus] --> g1[gates<br>code] --> v1[review<br>Fable] --> s1[sync<br>code] --> p1[pr<br>code] --> h1((human))
    g1 -. red ×2 .-> i1
    v1 -. FAIL ×1 .-> i1
    s1 -. conflict ×2 .-> x1[resolve<br>Opus] --> g1
  end
```

```mermaid
flowchart LR
  subgraph bug / hotfix
    pl[plan<br>Fable] --> im[implement<br>Opus] --> ga[gates<br>code] --> re[review<br>Fable] --> sy[sync<br>code] --> pr[pr<br>code] --> hu((human))
    ga -. red ×2 .-> im
    re -. FAIL ×1 .-> im
    sy -. conflict ×2 .-> rz[resolve<br>Opus] --> ga
  end
```

```mermaid
flowchart LR
  subgraph chore
    ic[implement<br>Opus] --> gc[gates<br>code] --> sc[sync<br>code] --> pc[pr<br>code] --> hc((human))
    gc -. red ×2 .-> ic
    sc -. conflict ×2 .-> rc[resolve<br>Opus] --> gc
  end
  subgraph research
    rr[research<br>Sonnet] --> jj[judge<br>Fable] --> ee((end))
  end
  subgraph merge-pr
    rs[resolve<br>Opus] --> gm[gates<br>code] --> rv[review<br>Fable] --> mg[merge<br>code] --> en((end))
    gm -. red ×2 .-> rs
    rv -. FAIL ×1 .-> rs
  end
```

Boxes with a model name are agent steps (`claude -p` inside the VM); `code` boxes are code steps (control-plane scripts executed against the VM). Dotted lines are send-backs; past the limit the run goes to `human`.

`sync` merges the latest base right before the PR is opened (built into the runner; ADR-0032), so a PR opened after a parallel run's PR was merged does not come out CONFLICTING. A conflict goes back to the implementer (`resolve`), not to a human; only after two failed resolutions does the run go to `human`.

## How to choose

```mermaid
flowchart TD
  Q1{Does it change code?} -->|no| RS[research]
  Q1 -->|yes| Q2{Merging an existing PR?}
  Q2 -->|yes| MP[merge-pr]
  Q2 -->|no| Q3{Is production down?}
  Q3 -->|yes| HF[hotfix]
  Q3 -->|no| Q4{Does it need judgement?}
  Q4 -->|no: docs, deps, settings| CH[chore]
  Q4 -->|yes| Q5{Bug or feature?}
  Q5 -->|bug| BG[bug]
  Q5 -->|feature| FT[feature]
```

intake follows this guidance. When in doubt, **bug** (which has a plan and a review) is the safe choice; keep chore for things where a mistake has little impact.

## Roles and models

| Role | Class | Model (`routes.env`) | Output |
|---|---|---|---|
| planner | judgment | Fable 5.1 | `plan.md` |
| researcher | research | Sonnet 5 | `research.md` / `summary.md` |
| implementer | coding | Opus 5 | git commits + `report.md` |
| reviewer | judgment | Fable 5.1 | `review.md` |

A step can override with `model_class`. For a one-off change use the environment variable `CLAUDE_MODEL`.

## Measured times

Measured in the maintainer's environment (2026-09).

| Run | Workflow | Agent | Gates | Result |
|---|---|---|---|---|
| Dependency update in a Node monorepo (a 3-line diff) | bug-like | 455 s | 6 min | PR |
| Fixing time-dependent Next.js tests (kumitate) | bug-like | 582 s | 16 min | PR, all 6 gates green |
| Investigating a Node project's CI gate | research | 232 s | none | summary.md |
| Merging an existing PR in a Rails + MySQL project | merge-pr | resolve 16 min + review 2 min | 57 min | merged |
| Investigating kumitate's CI | research | 433 s | none | summary.md |

Most of the time is spent in gates. Agents finish in minutes.

## Adding your own workflow

Adding one file `workflow/kit/workflows/<name>.yml` makes it available as a `kind` in `kb new`. The format is described in [workflow yml](../reference/workflow-yml.md). Steps use the four existing roles or the code steps (`gates.sh` / `pr-create.sh` / `pr-merge.sh`, plus the runner's built-in `sync-base`); a new role needs `workflow/kit/roles/<role>.md`.
