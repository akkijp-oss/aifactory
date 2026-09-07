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
| `stack` | string | | One-line technology stack | Top of the prompt |
| `facts` | array | | Facts told to the agent every time, one per line | The "Project" section of the prompt |
| `review_points` | array | | Project-specific points the reviewer must always check | Added only to the reviewer's prompt |
| `forbidden` | array | | Project-specific prohibitions | Added to every role's prompt as "Forbidden in this project" |
| `workflow_overrides` | object | | workflow name → overrides (v1: `base_branch` only) | Base decision |
| `known_red_gates` | array | | Names of gates already red on the base branch (names from `gates.sh`) | The runner downgrades FAIL to INFO and does not send the agent back to "fix it" |


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
- `known_red_gates` is temporary. Remove entries once the fixing PR is merged
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

How to write it: [Add a project](../guides/add-project.md#gates-sh).

`computer_use: true` enables the VM-local computer MCP on `macos-pull` / `windows-pull`. It defaults to disabled. See [setup](../guides/computer-use.md).

`backend: linux-pull` uses a standalone Linux instance. Set `app_dir` to `<work_root>/app` and use a `.sh` gate. Supports `computer_use: true`. See [Linux setup](../guides/linux-worker.md).
