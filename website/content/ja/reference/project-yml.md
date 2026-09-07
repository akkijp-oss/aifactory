# project.yml

`project.yml` には、対象リポジトリ、アプリの場所、テストの実行方法、禁止事項など、プロジェクト固有の情報を記述します。作業手順は共通のワークフローで定義します（ADR-0009）。

保存先は `$AIFACTORY_WORKSPACE/projects/<pj>/project.yml` です。ここにない場合は `examples/projects/<pj>/project.yml` を読み込みます。ファイルの形式は `workflow/kit/schema/project.schema.json` で検証されます。

## 項目

| 項目 | 型 | 必須 | 意味 | runner がどう使うか |
|---|---|---|---|---|
| `name` | string | ✅ | プロジェクト slug。`sandbox take <pj>` と `projects/<pj>/` に一致 | 識別 |
| `display_name` | string | | 表示名 | 依頼文の「名前」 |
| `repo` | string | ✅ | `owner/name` | PR の宛先、merge-pr の `gh pr view` |
| `base_branch` | string | ✅ | 通常の作業ブランチの元と PR の宛先 | ブランチ作成、`gh pr create --base` |
| `hotfix_base` | string | | hotfix の宛先（省略時は `base_branch`） | ワークフローが `base_branch: hotfix_base` のとき |
| `app_dir` | string | ✅ | VM 内のアプリのディレクトリ（リポジトリ直下と違うことがある） | エージェントの作業ディレクトリ、`gates.sh` の作業ディレクトリ |
| `url` | string | | VM 内から見たアプリの URL | 画面確認（将来） |
| `gates` | string | ✅ | ゲートスクリプト。`projects/<pj>/` からの相対パス | スクリプトが担当する工程 `gates.sh` が VM に scp して実行 |
| `stack` | string | | 1 行の技術スタック | 依頼文の冒頭 |
| `facts` | array | | エージェントに毎回伝える事実。1 項目 1 行 | 依頼文の「プロジェクト」節 |
| `review_points` | array | | reviewer が必ず見る観点（プロジェクト固有） | reviewer の依頼文だけに追加 |
| `forbidden` | array | | エージェントがやってはいけないこと（プロジェクト固有） | 全役割の依頼文に「このプロジェクトで禁止」として追加 |
| `workflow_overrides` | object | | ワークフロー名 → 上書き（v1 は `base_branch` のみ） | base の決定 |
| `known_red_gates` | array | | base ブランチで既に失敗するゲート名（`gates.sh` の名前） | runner が FAIL を INFO（参考情報）として扱うように変更し、エージェントに「直せ」と戻さない |


Windowsでは `backend: windows-pull`、登録済みの `worker`、`app_dir: <work_root>/app`、`.ps1` のゲートを指定します。[Windowsワーカーの導入手順](../guides/windows-worker.md)を参照してください。

## 例（kumitate）

同梱の `examples/projects/kumitate/project.yml` です。

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
  - "リポジトリ直下は台帳と docs。アプリは apps/kumitate/（pnpm workspace: apps/{web,marketing,scheduler}、packages/{db,dsl,generate,app-sdk,feedback-widget}）"
  - "依存は `pnpm install --frozen-lockfile`（apps/kumitate で）。pnpm は packageManager の版に固定"
  - "DB は VM ローカルの PostgreSQL 16（role kumitate / DB kumitate、DATABASE_URL は apps/kumitate/.env）。migrate は `pnpm --filter @kumitate/db db:migrate`"
  - "CI のゲート: `pnpm tokens:check` / typecheck（dsl, db, generate, web, marketing）/ lint（web, marketing）/ test（同 5 パッケージ）/ `pnpm --filter @kumitate/web test:dom`"
  - "個別テストは `pnpm --filter @kumitate/web exec vitest run <file>`"
  - "テストが生成する apps/kumitate/packages/db/.data/ 配下は追跡外。git add しない"
  - "既知の問題（2026-09-06）: apps/web の calendar 表題テスト 2 件がフィクスチャ 2026-08 固定で時間依存"
  - "`tokens/` はデザイントークンの単一情報源。変えたら `pnpm tokens:build` して差分をコミット"
review_points:
  - "apps/web のテストを skip / 削除して緑にしていないか"
  - "tokens/ の変更に tokens:build の差分が伴っているか"
  - "packages/db の schema 変更に migration が伴っているか（drizzle）"
forbidden:
  - "packages/db の migration を手で編集しない（drizzle-kit generate を使う）"
  - "deploy.yml / .github/workflows を変えない"
  - ".env の実値をコミットしない"
```

## 書き方の注意

- YAML の平文にバッククォートや `: ` を含める項目は `"..."` で囲む（PyYAML が誤読する）
- `facts` は「テストの実行方法」「生成物の置き場」「既知の問題（日付つき）」が特に効く。長くなったら `README` や `CLAUDE.md` の該当箇所を指すだけにする
- 「全プロジェクトで同じ注意」は書かない（`workflow/kit/roles/_common.md` へ）
- `known_red_gates` は一時的。直す PR がマージされたら消す
- 変更は次の run から効く

## 検証スクリプト: gates.sh

`gates.sh` は `project.yml` と同じディレクトリに置きます。VM 内で `$SANDBOX_APP_DIR` を作業ディレクトリとして実行し、各ゲートの結果を 1 行ずつ出力します。すべて成功した場合の終了コードは 0 です。

```
PASS typecheck
FAIL test (~/gates/test.log)
INFO audit-gate red (known on base; not a gate)
```

| 行 | 意味 |
|---|---|
| `PASS <name>` | 成功 |
| `FAIL <name> (<log>)` | 失敗。括弧内はログの保存先 |
| `INFO <name> …` | 情報扱い（`known_red_gates` か、スクリプト側で情報にしたもの） |

具体的な書き方は [プロジェクトを追加する](../guides/add-project.md#gates-sh) を参照してください。

`computer_use: true` は `macos-pull` / `windows-pull` でVM内のcomputer MCPを有効にする。既定は無効。[導入手順](../guides/computer-use.md)。

`backend: linux-pull` は単体Linuxインスタンスを使う。`app_dir` は `<work_root>/app`、`gates` は `.sh`。`computer_use: true` に対応。[Linux導入手順](../guides/linux-worker.md)。
