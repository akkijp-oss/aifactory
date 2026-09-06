# ADR-0016: 枠組み（リポジトリ）と運用データ（workspace）を分ける。置き場の判断は `lib/aifactory_paths.py` に 1 つ

日付: 2026-09-06 / 状態: 採用 / 決定者: メンテナ（「このプロジェクトを汎用的な OSS として公開できるようにしたい」）

## 状況
- リポジトリを Apache-2.0 で公開する方針になった。棚卸し（メンテナの workspace 側 `docs/oss-release-plan.md`。公開側は `docs/oss-release-checklist.md`）の結果、秘密情報は無かったが、**私有 PJ の情報がリポジトリ本体に混ざっていた**: PJ 定義（`sandbox/templates/<pj>/`。私有リポジトリ名・DB パスワード・焼き込み手順）、チケット（`kanban/tickets/`・`kanban.db`）、実行記録（`workflow/runs/`。プロンプトに私有 PJ の CLAUDE.md、work/ に私有コードの調査）、取り込み・配車のログ、インフラ台帳、他人の講演の全文文字起こし
- これらは「消す」ものではなく「このリポジトリの外に置く」ものである。枠組み（CLI・kit・手順・ADR・サイト）は汎用で、運用データは利用者ごとに違う
- 置き場の判断が `kb` / `run` / `intake` / `dispatch` / `console/lib/core.py` の 5 か所にリポジトリ相対で書かれていて、`KB_ROOT` だけが差し替えられた（ADR-0011 / 0013）

## 決定
- 運用データの根を **`AIFACTORY_WORKSPACE`**（既定 `<repo>/workspace/`、`.gitignore` 済み）に置く。中身は `projects/<pj>/`（PJ 定義）・`kanban/`（`kanban.db` / `tickets/` / `BOARD.md`）・`runs/`・`logs/`・`docs/`（私有メモ）
- 置き場の判断は **`lib/aifactory_paths.py` に 1 つ**。5 つの CLI はこれを import する。`KB_ROOT` / `CONSOLE_JOBS` の個別上書きは残す
- PJ 定義の探索順は `workspace/projects/<pj>` → `examples/projects/<pj>`。**`examples/projects/` に参照用の PJ 定義を同梱**する（現在は kumitate。メンテナが公開を許可）。同梱テストはこの例で動く
- kanban の `run` 列は run 名（例 `2026-09-06-kumitate-206`）を持つ。旧記録の `workflow/runs/<名前>` も同じ関数で解決する
- `sandbox/templates/` に残すのは枠組み側の雛形だけ（`base/`、`env.example`、`ssh_config.example`、`launchd/`）
- 旧配置からの移行は `bin/migrate-workspace.sh`（1 回限り。runner 実行中は拒む）。移行が済むまでの互換として、`AIFACTORY_WORKSPACE` 未設定かつ `kanban/kanban.db` がリポジトリに残っている間は旧配置を使う。この互換は次の版で外す

## 理由
- 公開リポジトリに私有データが混ざる事故を「置き場」で構造的に防ぐ。`.gitignore` の列挙で防ぐより壊れにくい
- 判断を 1 か所にすると、5 つの CLI で置き場の解釈が食い違わない（ADR-0015 と同じ考え方）
- 例を同梱すると、`git clone` して `kb new` → `kb run --dry-run` まで VM 無しで動き、テストも本番データに依存しない
- 互換モードを置くのは、この変更の最中にも別セッションの run が走っていたから（実機を止めずに切り替えるため）

## 結果（トレードオフ）
- 良い: 公開できる。`git status` に運用データが出ない。workspace を別リポジトリ（私有）にして履歴を持たせることもできる
- 悪い: `sandbox/templates/<pj>` を指していた文書・スクリプト・手癖を全部直す必要がある。`kb` の `run` 列の意味が変わる（解決関数で吸収）
- 未決: workspace を私有 git リポジトリにするか（現状は追跡外ディレクトリのまま）。公開サンプル PJ を「他人が clone できる」小さな公開リポジトリにするか（kumitate は私有）

## 追記（2026-09-06 夜）
- workspace は私有 git リポジトリにした（メンテナは `<you>/aifactory-workspace`）。公開サンプル PJ は aifactory 自身（`examples/projects/aifactory/`）で解決
- 置き場の指定に `~/.config/aifactory/workspace`（1 行のパス）を足した。優先順は環境変数 → 設定ファイル → 既定。シェルを経由しない起動（GUI から開いた Claude Code の MCP、launchd）でも同じ場所に届くようにするため
