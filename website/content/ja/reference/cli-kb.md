# kb（kanban CLI）

`kanban/bin/kb`。チケットの採番・状態・履歴を持つ台帳（SQLite）と、runner の呼び出し口。Python 3 標準ライブラリのみ。

```
kb new <pj> <kind> <title> [--body FILE|-] [--pr N] [--id N] [--status S] [--note TEXT]
kb list [--status S] [--pj P] [--all]
kb show <id>
kb start|review|done|reopen <id> [--note TEXT]
kb block <id> --note TEXT
kb set <id> [--status S] [--pr N] [--run DIR] [--note TEXT] [--kind K]
kb next [--pj P] [--json]
kb run <id> [--workflow W] [--dry-run] [--keep] [--resume]
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
| `todo` | 未着手 | 起票済み |
| `in_progress` | 実行中 | runner が実行中 |
| `review` | レビュー待ち | PR あり。人間待ち |
| `blocked` | 人間待ち | human 行き、異常終了、project.yml 無し。メモに理由 |
| `done` | 完了 | マージ済み、または PR 無しで終了 |

## コマンド

### new

```bash
kb new <pj> <kind> "<題名>" [--body FILE|-] [--pr N] [--id N] [--status S] [--note TEXT]
```

| 引数 | 意味 |
|---|---|
| `pj` | PJ 定義ディレクトリ（`$AIFACTORY_WORKSPACE/projects/<pj>/`、無ければ `examples/projects/<pj>/`）があること |
| `kind` | `workflow/kit/workflows/<kind>.yml` に一致すること（chore / bug / feature / hotfix / research / merge-pr） |
| `title` | 1 行目。70 字で切る |
| `--body` | 本文ファイル。`-` で標準入力。無ければ本文なし |
| `--pr` | merge-pr の対象 PR 番号。本文 2 行目に `pr: N` として書かれる |
| `--id` | 番号を指定（既定は `MAX(id)+1`、最小 100）。既にあればエラー |
| `--status` | 初期状態（既定 todo） |
| `--note` | メモ |

出力: `<id> <状態> <pj> <kind> <PR> <題名>` の 1 行と本文のパス。ファイル名の slug は題名の ASCII 部分から作り、無ければ kind。

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

`--pr` を変えると本文の `pr:` 行も書き換わる（runner が本文の `pr:` を読むため）。`--run` は run ディレクトリ名（`workspace/runs/` からの相対）。`--kind` は実在検証あり。すべて履歴に残り、BOARD.md が再生成される。

### run

```bash
kb run 204 [--workflow W] [--dry-run] [--keep] [--resume]
```

1. `pj` に `project.yml` が無ければエラー。`done` は `--dry-run` 以外エラー（`reopen` してから）
2. 状態を `in_progress` に、run ディレクトリ名（`<日付>-<pj>-<id>`。実体は `workspace/runs/` の下）を記録
3. `workflow/bin/run <pj> <id> <workflow> <本文のパス> [flags]` を呼ぶ。`--workflow` を渡すと `kind` も書き換わる
4. 終わったら `state.json` を読んで状態を進める（下表）

| state.json | 状態 | メモ |
|---|---|---|
| `pr_url` に `MERGED` | done | マージ済み URL |
| `pr_url` あり | review | PR 待ち URL |
| `result: end`、PR 無し | done | PR 無しで終了（research 等） |
| `result: human`、PR 無し | blocked | 人間へ（wip ブランチ） |
| `finished` 無し / 異常終了 | blocked | runner が異常終了 rc=N |

`--dry-run` は状態を変えない。終了コードは runner のもの（0 = end か PR あり、2 = human）。

### sync

```bash
kb sync 204 [--run DIR]
```

`state.json` を読み直して状態を合わせる。runner を直接呼んだとき、`kb run` が途中で落ちたとき、他セッションが回した run を取り込むときに使う。`finished` が無ければ `in_progress` のまま「次の step」をメモに書く。

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
