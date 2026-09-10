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
| `prepare` | string | | 貸出直後の準備スクリプト。`projects/<pj>/` からの相対パス | VM を取って checkout した直後に `app_dir` を作業ディレクトリとして 1 回だけ実行。失敗したらエージェントを起動せず `failure: prepare` で終わる |
| `stack` | string | | 1 行の技術スタック | 依頼文の冒頭 |
| `facts` | array | | エージェントに毎回伝える事実。1 項目 1 行 | 依頼文の「プロジェクト」節 |
| `review_points` | array | | reviewer が必ず見る観点（プロジェクト固有） | reviewer の依頼文だけに追加 |
| `forbidden` | array | | エージェントがやってはいけないこと（プロジェクト固有） | 全役割の依頼文に「このプロジェクトで禁止」として追加 |
| `workflow_overrides` | object | | ワークフロー名 → 上書き（v1 は `base_branch` のみ） | base の決定 |
| `known_red_gates` | array | | base ブランチで既に失敗するゲート名（`gates.sh` の名前） | runner が FAIL を INFO（参考情報）として扱うように変更し、エージェントに「直せ」と戻さない |
| `auto_merge` | boolean / object | | ゲート緑・レビュー PASS・CI 緑の PR を runner が `base_branch` へマージする（既定は無効） | `pr` の後の `automerge` 工程を回す。無い場合はその工程ごと飛ばして人間に渡す |


### `auto_merge`（自動マージ）

`true` と書くと既定値で有効になります。値を選ぶ場合は object で書きます。

```yaml
auto_merge:
  method: merge          # merge / squash / rebase（既定 merge）
  wait_min: 20           # CI の check が終わるのを待つ上限（分。既定 20）
  delete_branch: true    # マージ後に origin の作業ブランチを消す（既定 true）
  require_checks: true   # check が 0 本ならマージしない（既定 true。CI が無いプロジェクトは false）
```

マージするのは次を**すべて**満たすときだけです（ADR-0042）。

- `gates.txt` に `FAIL` が無い（`INFO`＝base でも失敗するゲートは差し支えありません）
- ワークフローに reviewer がいる場合、`review.md` の 1 行目が `# レビュー: PASS`
- PR が開いていて下書きでなく、宛先と head がその run のもの
- `gh pr checks` がすべて pass（30 秒ごとに `wait_min` まで待ち、1 本でも失敗したらそこで止まります）
- GitHub の判定が `MERGEABLE`

1 つでも欠けるときはマージせず、PR を開いたまま人間に渡します。理由は `state.json` の `error` に `automerge: CI 赤 (test)` のような 1 行で残り、板・コンソール・`run_show` から読めます。

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
- `auto_merge` は**すべてのワークフローに効く**。`hotfix_base: main` のプロジェクトで有効にすると、hotfix が `main` へ自動で入る
- `known_red_gates` は一時的。直す PR がマージされたら消す
- `known_red_gates` は手で書かなくても、runner が赤いゲートを base で回して確かめた分が run の記録に載る（下記）
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

引数でゲート名を渡されたら、その名前のゲートだけを実行してください（引数なしなら全部）。runner が base で確かめるときに使います。

## 貸出直後の準備: prepare

VM は run が終わるたびにテンプレート（`provision.sh` を焼いた時点）に戻り、base ブランチだけが進みます。この差（依存の追加、DB のマイグレーション）はゲートの赤として現れますが、エージェントには直せません。

`prepare` を指定すると、runner が VM を取って checkout した直後、最初のエージェント工程の前に 1 回だけ実行します。

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

失敗した場合、runner はエージェントを起動せずに終了します（`state.json` に `result: failed` と `failure: prepare`、工程の履歴は空、出力は `code-prepare.log`）。かんばんではチケットが `blocked` になります。macOS / Windows / Linux のワーカーでは、既存の `provision.sh` が毎回の貸出で同じ役目を果たします。

## base でも赤いゲート

ゲートが赤いとき、runner は**その赤いゲートだけ**を base（`origin/<base_branch>`）でも実行します。base でも赤ければ、そのゲートは実装工程に戻しません。

```
INFO strings red (also red on base; not a gate)
PASS feature

=== base check: origin/develop
BASE-CHECK origin/develop 675cdbc
FAIL strings (~/gates/strings.log)
```

残りに `FAIL` が無ければゲートは成功として次の工程へ進むので、実装への差し戻しを消費しません。確かめたゲート名は run の記録に残り、`sandbox_status` が `known_red_gates` に手で書いた値と合わせて返します。`project.yml` は書き換えません。

base を確認できなかった回（未コミットの変更を退避できない、`origin/<base_branch>` が無いなど）は `=== base check:` に `BASE-CHECK-SKIP` と理由が出て、格下げは行いません。base を見たあと作業ブランチへ戻し切れなかった場合は、赤が残っていなくても run を止めて人に返します。

具体的な書き方は [プロジェクトを追加する](../guides/add-project.md#gates-sh) を参照してください。

`computer_use: true` は `macos-pull` / `windows-pull` でVM内のcomputer MCPを有効にする。既定は無効。[導入手順](../guides/computer-use.md)。

`display: {width, height}` は `macos-pull` のゲスト画面の大きさ（幅800〜2560・高さ600〜2560で両方必須）。省略すると基準VMのまま（1024×768）。ワーカー設定にも同じキーがあり、優先順位はPJ定義 > ワーカー設定 > 省略。`scale` は未対応。[設定手順](../guides/computer-use.md)。

`backend: linux-pull` は単体Linuxインスタンスを使う。`app_dir` は `<work_root>/app`、`gates` は `.sh`。`computer_use: true` に対応。[Linux導入手順](../guides/linux-worker.md)。
