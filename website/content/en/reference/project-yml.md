# project.yml

`$AIFACTORY_WORKSPACE/projects/<pj>/project.yml` (else `examples/projects/<pj>/project.yml`). The definition holding only the project's **facts and policy**. Procedures (workflows) are never written here. Validated against `workflow/kit/schema/project.schema.json` (ADR-0009).

## Fields

| Field | Type | Required | Meaning | How the runner uses it |
|---|---|---|---|---|
| `name` | string | ✅ | Project slug. Matches `sandbox take <pj>` and `projects/<pj>/` | Identification |
| `display_name` | string | | Display name | "Name" in the prompt |
| `repo` | string | ✅ | `owner/name` | PR target, `gh pr view` for merge-pr |
| `base_branch` | string | ✅ | Source of work branches and PR target | Branch creation, `gh pr create --base` |
| `hotfix_base` | string | | Target for hotfix (defaults to `base_branch`) | When the workflow says `base_branch: hotfix_base` |
| `app_dir` | string | ✅ | App directory inside the VM (may differ from the repository root) | Agent cwd, `gates.sh` cwd |
| `url` | string | | App URL as seen from inside the VM | Screen checks (future) |
| `gates` | string | ✅ | Gate script, relative to `projects/<pj>/` | Copied to the VM and run by the `gates.sh` code step |
| `prepare` | string | | Setup script run right after checkout, relative to `projects/<pj>/` | Run once inside the VM with `app_dir` as cwd. If it fails, no agent is started and the run ends with `failure: prepare` |
| `stack` | string | | One-line technology stack | Top of the prompt |
| `facts` | array | | Facts told to the agent every time, one per line | The "Project" section of the prompt |
| `review_points` | array | | Project-specific points the reviewer must always check | Added only to the reviewer's prompt |
| `forbidden` | array | | Project-specific prohibitions | Added to every role's prompt as "Forbidden in this project" |
| `workflow_overrides` | object | | workflow name → overrides (v1: `base_branch` only) | Base decision |
| `known_red_gates` | array | | Names of gates already red on the base branch (names from `gates.sh`) | The runner downgrades FAIL to INFO and does not send the agent back to "fix it" |
| `auto_merge` | boolean / object | | Let the runner merge a PR into `base_branch` when the gates are green, the review is PASS and CI is all green (off by default) | Runs the `automerge` step after `pr`. Without it the step is skipped entirely and the PR is handed to a human |


### `auto_merge`

Writing `true` turns it on with the defaults. Write an object to choose the values.

```yaml
auto_merge:
  method: merge          # merge / squash / rebase (default merge)
  wait_min: 20           # How long to wait for CI checks, in minutes (default 20)
  delete_branch: true    # Delete the working branch on origin after merging (default true)
  require_checks: true   # Do not merge when there are zero checks (default true; set false for a project without CI)
```

It merges only when **all** of these hold (ADR-0042):

- No `FAIL` in `gates.txt` (`INFO`, i.e. a gate that is red on base too, is fine)
- If the workflow has a reviewer, the first line of `review.md` is `# レビュー: PASS`
- The PR is open, not a draft, and its base and head are the ones of this run
- Every `gh pr checks` entry passed (polled every 30 seconds up to `wait_min`; a single failure stops it right away)
- GitHub reports `MERGEABLE`

If anything is missing it does not merge: the PR stays open and goes to a human. The reason is kept on one line in `error` in `state.json`, such as `automerge: CI 赤 (test)`, and is readable from the board, the console and `run_show`.

For Windows, set `backend: windows-pull`, a registered `worker`, `app_dir: <work_root>/app`, and a `.ps1` gate. See [Windows worker setup](../guides/windows-worker.md).

## Example (kumitate)

The bundled `examples/projects/kumitate/project.yml`. The file itself is written in Japanese; the strings are shown as they are, with the meaning in brackets where it helps.

```yaml
name: kumitate
display_name: kumitate
repo: akkijp/kumitate
base_branch: develop
hotfix_base: main
app_dir: /home/dev/app/apps/kumitate
url: http://localhost:3000
gates: gates.sh
stack: "pnpm 10 monorepo（turbo）/ Node 22 / Next.js（apps/web :3000）/ PostgreSQL 16 + pgvector / drizzle / vitest"
facts:
  - "リポジトリ直下は台帳と docs。アプリは apps/kumitate/（pnpm workspace: apps/{web,marketing,scheduler}、packages/{db,dsl,generate,app-sdk,feedback-widget}）"   # repo root is ledgers and docs; the app is apps/kumitate/
  - "依存は `pnpm install --frozen-lockfile`（apps/kumitate で）。pnpm は packageManager の版に固定"   # dependencies; pnpm pinned by packageManager
  - "DB は VM ローカルの PostgreSQL 16（role kumitate / DB kumitate、DATABASE_URL は apps/kumitate/.env）。migrate は `pnpm --filter @kumitate/db db:migrate`"
  - "CI のゲート: `pnpm tokens:check` / typecheck（dsl, db, generate, web, marketing）/ lint（web, marketing）/ test（同 5 パッケージ）/ `pnpm --filter @kumitate/web test:dom`"   # CI gates
  - "個別テストは `pnpm --filter @kumitate/web exec vitest run <file>`"   # running a single test
  - "テストが生成する apps/kumitate/packages/db/.data/ 配下は追跡外。git add しない"   # generated test data is untracked
  - "既知の問題（2026-09-06）: apps/web の calendar 表題テスト 2 件がフィクスチャ 2026-08 固定で時間依存"   # known issue: 2 time-dependent calendar tests
  - "`tokens/` はデザイントークンの単一情報源。変えたら `pnpm tokens:build` して差分をコミット"   # tokens/ is the design-token source of truth
review_points:
  - "apps/web のテストを skip / 削除して緑にしていないか"   # tests not skipped or deleted to go green
  - "tokens/ の変更に tokens:build の差分が伴っているか"
  - "packages/db の schema 変更に migration が伴っているか（drizzle）"   # schema changes come with a migration
forbidden:
  - "packages/db の migration を手で編集しない（drizzle-kit generate を使う）"   # do not hand-edit migrations
  - "deploy.yml / .github/workflows を変えない"   # do not change deploy.yml / workflows
  - ".env の実値をコミットしない"   # do not commit real .env values
```

## Writing notes

- Quote values containing backticks or `: ` with `"..."` (PyYAML misreads them otherwise)
- The most useful `facts` are "how to run tests", "where generated files go" and "known issues (dated)". If they get long, just point at the relevant part of the README or `CLAUDE.md`
- Do not write rules that apply to every project (those go in `workflow/kit/roles/_common.md`)
- `auto_merge` applies to **every workflow**. Turning it on in a project with `hotfix_base: main` lets a hotfix land on `main` automatically
- `known_red_gates` is temporary. Remove entries once the fixing PR is merged
- You do not have to fill in `known_red_gates` by hand: the runner records the gates it confirmed red on base in the run record (see below)
- Changes take effect from the next run

## Its partner: gates.sh

`gates.sh` in the same directory. Runs inside the VM with `$SANDBOX_APP_DIR` as cwd, one line per gate, exit 0 only when everything is green.

```
PASS typecheck
FAIL test (~/gates/test.log)
INFO audit-gate red (known on base; not a gate)
```

| Line | Meaning |
|---|---|
| `PASS <name>` | Green |
| `FAIL <name> (<log>)` | Red, with the log location |
| `INFO <name> …` | Informational (from `known_red_gates`, or made informational by the script) |

When gate names are passed as arguments, run only those gates (no arguments means all of them). The runner uses this when it checks the base branch.

## Setup right after checkout: prepare

The VM rolls back to its template (the state when `provision.sh` was baked) after every run, while the base branch keeps moving. That gap — new dependencies, new database migrations — shows up as red gates that the agent cannot fix.

With `prepare` set, the runner runs the script once right after it takes the VM and checks out the branch, before the first agent step.

```yaml
gates: gates.sh
prepare: prepare.sh
```

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "${SANDBOX_APP_DIR:-$HOME/app}"
pnpm install --frozen-lockfile
pnpm --filter @kumitate/db db:migrate
```

If it fails, the runner ends the run without starting any agent (`result: failed` and `failure: prepare` in `state.json`, an empty step history, output in `code-prepare.log`), and the kanban ticket becomes `blocked`. On the macOS / Windows / Linux workers the existing `provision.sh` already plays the same role on every lease.

## Gates that are red on base too

When a gate is red, the runner runs **only the red gates** against the base branch (`origin/<base_branch>`) as well. Gates that are red on base too are not sent back to the implement step.

```
INFO strings red (also red on base; not a gate)
PASS feature

=== base check: origin/develop
BASE-CHECK origin/develop 675cdbc
FAIL strings (~/gates/strings.log)
```

If no `FAIL` is left, the gates step passes and the run moves on, without spending one of its trips back to the implementer. The confirmed gate names are kept in the run record, and `sandbox_status` returns them together with whatever was written by hand in `known_red_gates`. `project.yml` itself is never rewritten.

If base could not be checked (uncommitted changes could not be stashed, `origin/<base_branch>` is missing, and so on), the reason is printed under `=== base check:` as `BASE-CHECK-SKIP` and nothing is downgraded. If the working tree cannot be put back on the working branch after the base check, the run stops and goes to a human even when no gate is left red.

How to write it: [Add a project](../guides/add-project.md#gates-sh).

`computer_use: true` enables the VM-local computer MCP on `macos-pull` / `windows-pull`. It defaults to disabled. See [setup](../guides/computer-use.md).

`display: {width, height}` sets the guest screen size on `macos-pull` (800-2560 wide, 600-2560 high; both keys required). Without it the guest keeps the base image's 1024x768. The worker configuration takes the same key, and precedence is project definition, then worker configuration, then neither. `scale` is unsupported. See [setup](../guides/computer-use.md).

`backend: linux-pull` uses a standalone Linux instance. Set `app_dir` to `<work_root>/app` and use a `.sh` gate. Supports `computer_use: true`. See [Linux setup](../guides/linux-worker.md).
