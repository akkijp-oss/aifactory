# run（ワークフロー runner）

`workflow/bin/run` は、ワークフローの定義に従って各工程を進めるプログラム（runner）です。通常は `kb run` 経由で呼び出します。現在の実装は v1 で、Python 3 と `pyyaml`、`jsonschema` を使用します。

```
workflow/bin/run <pj> <task-id> <workflow> <ticket.md> [--dry-run] [--keep] [--resume] [--wait[=秒]]
```

| 引数 | 意味 |
|---|---|
| `pj` | `project.yml` があるプロジェクト（`$AIFACTORY_WORKSPACE/projects/<pj>/`、なければ `examples/projects/<pj>/`） |
| `task-id` | `sandbox take` に渡す ID（3 桁以上。kanban が採番したもの） |
| `workflow` | `workflow/kit/workflows/` の名前 |
| `ticket.md` | チケット。1 行目が題名、任意で 2 行目 `pr: N` |
| `--dry-run` | VM を触らず、定義の検証と依頼文の組み立てだけ。`workspace/runs/…-dry/` に出す |
| `--keep` | 終了後に release しない（中を見たいとき） |
| `--resume` | 既に貸出中の VM で、`state.json` の次の工程から続ける |
| `--wait[=秒]` | プールに空きがないとき、空くまで待って `sandbox take` をやり直す（単独なら 3600 秒。再試行の間隔は `AIFACTORY_WAIT_POLL_S` 秒、既定 30） |

## 終了コード

| コード | 意味 |
|---|---|
| 0 | `result: end`、または PR ができた |
| 1 | 定義の誤り、引数の誤り、工程がない |
| 2 | `result: human`（PR なし） |

## 動き

1. `kit/workflows/<wf>.yml` とプロジェクトの `project.yml` を読み、`kit/schema/` で検証
2. base ブランチを決める（ワークフローの `base_branch: hotfix_base` → project の `hotfix_base`、`workflow_overrides` で上書き）
3. merge-pr（本文に `pr: N`）なら `gh pr view` で head / base を取り、head を作業ブランチにする。それ以外は `sandbox/<id>-<wf>-<slug>`
4. `workspace/runs/<日付>-<pj>-<id>/` を作る。既にあり `--resume` でなければ前回を `-attemptN` に退避。`ticket.md` と `state.json` を置く
5. `sandbox take <pj> <id>`。VM 内で base を fetch し作業ブランチを切る。チケットを `~/work/<id>/ticket.md` に。`--wait` があり「空きなし」で失敗したときは、`current` を `wait-vm` にして空くまで待ち、take をやり直す（ADR-0031）
6. 工程を順に実行（下）。`end` か `human` に着くまで
7. `human` なら `origin/sandbox/<id>-<wf>-wip` に push して成果を退避
8. `~/work/<id>/` を `workspace/runs/…/work/` に回収。`--keep` でなければ `sandbox release`
9. `state.json` に `result` / `pr_url` / `wip_branch` / `finished` / `elapsed_s`

`--wait` の上限を超えたときは、`result: failed` に加えて `failure: "wait_timeout"` と `waited_s`（待った秒数）を残して終了コード 2 で終わります。`kb` はこの目印を見て、チケットを `blocked` ではなく `todo` に戻します。

### エージェントが担当する工程

- 依頼文を 8 層で組み立て、`workspace/runs/…/prompt-<step>-<n>.md` に残し、VM の `/home/dev/prompt.md` に置く
- モデル: 工程の `model_class` → 役割の既定クラス → `routes.env`。環境変数 `CLAUDE_MODEL` が最優先
- 実行: `cd $SANDBOX_APP_DIR && timeout <timeout_min>m claude -p "$(cat /home/dev/prompt.md)" --model <model> --output-format stream-json --verbose`。runner がイベントを 1 行ずつ受け、人が読める形を `agent-<step>-<n>.log` に逐次書く（時刻、ツール呼び出し ▶、結果の先頭 3 行 ↳、result と費用）。生の JSON は `agent-<step>-<n>.jsonl`
- 実行中は `state.json` の `current` に `{step, kind, log, since}` が入る（工程が終わると `null`）
- 合否: `outputs` のファイル（`git` / `pr_url` を除く）が全部 `~/work/<id>/` にあるか

### スクリプトが担当する工程

- `kit/steps/<script>` を Mac で実行。出力は `code-<step>-<n>.log` に逐次書く（`current` も同様に入る）
- 渡す env: `PJ` `TASK` `RUN_DIR` `PROJECT_DIR` `GATES` `WORK` `APP_DIR` `BASE` `BRANCH` `WORKFLOW` `TITLE` `PR_NUMBER` `KNOWN_RED`
- 実行前に GitHub App トークンを払い出し直す（`sandbox reinject`）
- 合否: 終了コード

### transition

| 工程の書き方 | 結果 | 次 |
|---|---|---|
| `next: X` | 成功 | X |
| `next: X` | 失敗 | human（分岐のない工程が失敗したら人間へ） |
| `on_pass: X` / `on_fail: {goto: Y, max_loops: N, else: Z}` | 成功 | X |
| 同上 | 失敗、ループ回数 < N | Y（回数を `state.json` の `loops` に数える） |
| 同上 | 失敗、回数 ≥ N | Z |

修正をやり直すときは、前回の結果を「前回の結果（直すこと）」として次の依頼文に添えます。検証（gates）からエージェントの工程に戻す場合は、変更前のブランチでも失敗している項目について、修正せずに報告するよう指示します。

## state.json

```json
{
  "pj": "kumitate", "task": "204", "workflow": "bug",
  "branch": "sandbox/204-bug-fix-calendar-test", "base": "develop",
  "started": "2026-09-06T12:00:00",
  "history": [
    {"step": "plan", "ok": true, "next": "implement", "at": "…"},
    {"step": "gates", "ok": false, "next": "implement", "at": "…"}
  ],
  "loops": {"gates->implement": 1},
  "current": null,
  "next": "end",
  "result": "end",
  "pr_url": "https://github.com/akkijp/kumitate/pull/300",
  "wip_branch": "",
  "finished": "2026-09-06T12:31:00",
  "elapsed_s": 1860
}
```

merge-pr の merge 工程が成功すると、`pr_url` の末尾に ` MERGED` が付きます。工程の実行中は、`current` に `{"step": "implement", "kind": "agent", "log": "agent-implement-1.log", "since": "…"}` のような情報が入ります。Web コンソールはこの情報を使って、実行中のログを表示します。

## 環境変数

| 変数 | 意味 |
|---|---|
| `CLAUDE_MODEL` | 全エージェントが担当する工程のモデルを 1 回だけ上書き |
| `MERGE_METHOD` | merge-pr のマージ方法（merge / squash / rebase。既定 merge） |

## 過去に起きた問題と対処

| 罠 | 対処 |
|---|---|
| base で既に失敗するゲートをエージェントに「直せ」と戻すと範囲外の修正をする | `project.yml` の `known_red_gates`。runner が FAIL → INFO に格下げ |
| `git add -A` の自動コミットが生成物を拾う | エージェントの約束で `git add -u` に限定 |
| 変数の直後に全角括弧（`$loop）`）で bash が未定義変数と見る | 常に `${var}`。runner を Python にした理由の一つ |
| 実行中の bash スクリプトを編集すると壊れる | 実行中の run があるときはスクリプトを触らない |
| 同じ日に同じチケットを再実行すると `workspace/runs/` を上書きした | 前回を `-attemptN` に退避してから作る |
| GitHub App トークンの 1 時間失効が長い run で顕在化 | スクリプトの実行前に払い出し直す |
| 子プロセスの出力に壊れた UTF-8 が混ざると runner も異常終了した | `errors="replace"` で読む |
