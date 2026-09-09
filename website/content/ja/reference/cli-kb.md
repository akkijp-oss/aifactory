# kb（kanban CLI）

`kanban/bin/kb` は、チケットの作成、状態の更新、履歴の確認、runner の呼び出しを行う CLI です。状態と履歴を SQLite に保存します。Python 3 の標準ライブラリだけで動作します。

```
kb new <pj> <kind> <title> [--body FILE|-] [--pr N] [--id N] [--status S] [--note TEXT]
kb list [--status S] [--pj P] [--all]
kb show <id>
kb start|review|done|reopen <id> [--note TEXT]
kb block <id> --note TEXT
kb set <id> [--status S] [--pr N] [--run DIR] [--note TEXT] [--kind K]
kb append <id> [--section S] [--text T]
kb next [--pj P] [--json]
kb run <id> [--workflow W] [--dry-run] [--keep] [--resume] [--from [STEP]] [--branch B] [--wait [分]]
kb sync <id> [--run DIR]
kb history <id>
kb render
```

## 置き場

| もの | パス |
|---|---|
| DB（正本） | `$AIFACTORY_WORKSPACE/kanban/kanban.db`（既定 `<repo>/workspace/kanban/`。git 追跡外） |
| 本文（正本） | 同 `kanban/tickets/<id>-<pj>-<slug>.md` |
| ボード（生成物） | 同 `kanban/BOARD.md` |
| 置き場の差し替え | 環境変数 `AIFACTORY_WORKSPACE=<dir>` で workspace ごと、`KB_ROOT=<dir>` で DB・tickets・BOARD だけを別に置く（テスト用） |

## 状態

| 状態 | 表示 | 意味 |
|---|---|---|
| `todo` | 未着手 | チケット作成済み |
| `in_progress` | 実行中 | runner が実行中 |
| `review` | レビュー待ち | PR あり。人間待ち |
| `blocked` | 人間待ち | human 行き、異常終了、project.yml なし。メモに理由 |
| `done` | 完了 | マージ済み、または PR なしで終了 |

## コマンド

### new

```bash
kb new <pj> <kind> "<題名>" [--body FILE|-] [--pr N] [--id N] [--status S] [--note TEXT]
```

| 引数 | 意味 |
|---|---|
| `pj` | プロジェクト定義ディレクトリ（`$AIFACTORY_WORKSPACE/projects/<pj>/`、なければ `examples/projects/<pj>/`）があること |
| `kind` | `workflow/kit/workflows/<kind>.yml` に一致すること（chore / bug / feature / hotfix / research / merge-pr） |
| `title` | 1 行目。70 字で切る |
| `--body` | 本文ファイル。`-` で標準入力。なければ本文なし |
| `--pr` | merge-pr の対象 PR 番号。本文 2 行目に `pr: N` として書かれる |
| `--id` | 番号を指定（既定は `MAX(id)+1`、最小 100）。既にあればエラー |
| `--status` | 初期状態（既定 todo） |
| `--note` | メモ |

`<id> <状態> <pj> <kind> <PR> <題名>` の 1 行と、本文ファイルのパスを出力します。ファイル名の slug は題名に含まれる ASCII 文字から作ります。該当する文字がなければ `kind` を使います。

### list / show / next

```bash
kb list                          # done 以外
kb list --status review          # 状態で絞る
kb list --pj kumitate --all      # PJ で絞る。--all で done も
kb show 204                      # 全項目 + 本文
kb next                          # 最も古い todo を 1 件
kb next --pj kumitate --json     # JSON（dispatch や外部ツール向け。path に本文の絶対パス）
```

### 状態を進める

```bash
kb start 204                     # → in_progress
kb review 204                    # → review
kb done 204 --note "マージ済み"    # → done
kb block 204 --note "本番影響の判断待ち"   # → blocked（--note 必須）
kb reopen 204                    # → todo
kb set 204 --status review --pr 300 --run 2026-09-06-kumitate-204 --note "…" --kind feature
```

`--pr` を変更すると、runner が参照する本文の `pr:` 行も更新されます。`--run` には、`workspace/runs/` からの相対パスで run ディレクトリ名を指定します。`--kind` は、指定した種別が存在するか確認されます。変更はすべて履歴に残り、`BOARD.md` が再生成されます。

`kb set 204 --note ''` はメモを空に戻します（DB では NULL）。項目を渡さなければその項目は変更しません。MCP と HTTP API（`console`）では、`note` は「キーがあれば空文字列でも渡す（= 消す）、キーがなければ触らない」として扱います。以前は空文字列を未指定として無視していました。`status` / `kind` / `pr` は従来どおり、空文字列を未指定として無視します。

### append

```bash
kb append 204 --section "PM 補足" --text "218 の `._*` は AppleDouble"
kb append 204 --section "PM 補足" < memo.md      # --text がなければ標準入力から読む
```

チケット本文の**末尾**に追記します。`--section` を付けると `## <見出し>` を先に書きます。本文が空、またはチケットの本文ファイルがなければエラー（終了コード 1）です。

挿入位置は末尾に固定しています。`## 完了条件` の手前に入れないのは、節を見分ける仕組みが `kb` になく、末尾なら変更が 1 か所で済むためです。後から書き足したものは見出しで見分けます。

追記そのものは本文（ファイルが正本）に残ります。履歴には `body  - → append 24字 (PM 補足)` の形で、いつ・どれだけ足したかだけが残ります（`history` は `field` / `old` / `new` の 3 列で、差分は保持しません）。

### run

```bash
kb run 204 [--workflow W] [--dry-run] [--keep] [--resume] [--from [STEP]] [--branch B] [--wait [分]]
```

1. `pj` に `project.yml` がなければエラー。`done` は `--dry-run` 以外エラー（`reopen` してから）
2. 状態を `in_progress` に、run ディレクトリ名（`<日付>-<pj>-<id>`。実体は `workspace/runs/` の下）を記録
3. `workflow/bin/run <pj> <id> <workflow> <本文のパス> [flags]` を呼ぶ。`--workflow` は今回の実行方法だけを変え、`kind` は変わりません（履歴に `workflow → <名前>` が残り、`runs/<run>/state.json` の `workflow` が正。ADR-0030）。`--workflow` なしの `--resume` は、その `state.json` の `workflow` で再開します
4. 終わったら `state.json` を読んで状態を進める（下表）

`--wait` を付けると、VM のプールに空きがないときに失敗せず、空くまで待ってから実行します（分。値を省くと 60 分）。待っている間、チケットは `in_progress` のままで、コンソールとボードには「VM の空き待ち」と経過時間が出ます。上限を超えたときだけチケットは `todo` に戻り、理由がメモに残ります（ADR-0031）。

`--from` を付けると、人間待ちで終わった run を**新しい VM**で、その続き（記録の退避ブランチ）から指定の工程だけやり直します。工程を省くと、記録に残った「やり直す工程」から始まります。前回の run の名前は環境変数で runner に渡り、前回の成果物とレビュー指摘が新しい VM に持ち込まれます（ADR-0034）。`--resume` とは併用できません。

| state.json | 状態 | メモ |
|---|---|---|
| `pr_url` に `MERGED` | done | マージ済み URL |
| `pr_url` あり | review | PR 待ち URL |
| `result: end`、PR なし | done | PR なしで終了（research 等） |
| `result: human`、PR なし | blocked | 人間へ（wip ブランチ） |
| `result: failed` | blocked | VM を取得できず工程が始まらなかった（`error` の最終行をメモに残す） |
| `result: failed`、`failure: wait_timeout` | todo | `--wait` の上限まで待っても空きが出なかった。直す所はないので未着手に戻す |
| `finished` なし、runner が rc≠0 | blocked | runner が記録を残さず終了 rc=N |

`--dry-run` ではチケットの状態を変更しません。終了コードは runner の値をそのまま返します。0 は正常終了または PR の作成完了、2 は PR がない状態での `human` 終了を表します。

### sync

```bash
kb sync 204 [--run DIR]
```

`state.json` を読み直し、チケットの状態に反映します。runner を直接呼び出した場合、`kb run` が途中で終了した場合、別のセッションの実行結果を反映する場合に使います。`finished` がなければ状態は `in_progress` とし、次の工程をメモに記録します。

### history / render

```bash
kb history 204                   # 日時 / 項目 / 旧 → 新
kb render                        # BOARD.md を再生成（通常は自動）
```

## 終了コード

| コード | 意味 |
|---|---|
| 0 | 成功 |
| 1 | 引数や存在チェックのエラー（`[kb] error: …` を標準エラーに） |
| runner のコード | `kb run` は runner の終了コードをそのまま返す |

## 実装メモ

- テーブルは `tickets`（id, pj, kind, title, status, file, pr, run, note, created, updated）と `history`（ticket, at, field, old, new）
- 単一 Mac 前提。複数マシンで同じ DB を書くと衝突する
- 判断はしない。LLM を呼ばない
