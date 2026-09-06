# workflow エンジン

このページで分かること: workflow の 5 つの語彙、YAML の形、runner がそれをどう実行するか、依頼文の組み立て。

## 5 つの語彙

| 語彙 | 意味 | 置き場 |
|---|---|---|
| **workflow** | チケット種別ごとの手順。step の並びと分岐 | `workflow/kit/workflows/<name>.yml` |
| **step** | 1 回の呼び出し。担い手は **role**（agent）か **code**（スクリプト）のどちらか | workflow の中 |
| **role** | agent の人格と権限。モデルのクラス、憲法、出力の型 | `workflow/kit/roles/<role>.md` |
| **artifact** | step の入出力。**必ずファイル**。VM の `~/work/<id>/` に置き、終了時に `workspace/runs/<run>/work/` へ回収 | workflow の `inputs` / `outputs` |
| **transition** | 結果に応じた次の行き先。ループ回数の上限つき。`human` = 人間に渡して終了 | step の `next` / `on_pass` / `on_fail` |

## YAML の形

`bug.yml` の gates と review です。

```yaml
name: bug
description: 不具合修正。計画 → 再現テスト → 修正 → ゲート → レビュー → PR
inputs: [ticket.md]
steps:
  - id: plan
    role: planner
    brief: |
      再現条件を最初に確定させる。再現テストをどこに書くかまで計画に含める。
    outputs: [plan.md]
    next: implement

  - id: implement
    role: implementer
    brief: |
      先に再現テストを書いて赤を確認してから直す。
    inputs: [plan.md]
    outputs: [git, report.md]
    next: gates

  - id: gates
    code: gates.sh                 # code step。kit/steps/gates.sh が VM 内で PJ の gates.sh を実行
    outputs: [gates.txt]
    on_pass: review
    on_fail: { goto: implement, max_loops: 2, else: human }

  - id: review
    role: reviewer
    inputs: [plan.md, report.md, gates.txt]
    outputs: [review.md]
    on_pass: pr
    on_fail: { goto: implement, max_loops: 1, else: human }

  - id: pr
    code: pr-create.sh
    inputs: [plan.md, report.md, review.md, gates.txt]
    outputs: [pr_url]
    next: human
```

- `role` か `code` のどちらか 1 つ。両方は書けない
- `outputs` の `git` は「作業ブランチにコミットせよ」、`pr_url` は「PR の URL を置け」の意味で、ファイルとしては回収しない
- `brief` は step 固有の追加指示。役割の憲法（`roles/<role>.md`）の後に貼られる
- `model_class`（judgment / research / coding）で役割の既定クラスを上書きできる。`timeout_min`（既定 60）で agent の上限時間

正しさは `workflow/kit/schema/workflow.schema.json` で検証され、runner は起動時に必ず行います。

## runner の動き

```mermaid
flowchart TD
  A[定義を読み schema 検証] --> B[ブランチ名を決める<br>sandbox/id-wf-slug]
  B --> C[sandbox take]
  C --> D{次の step}
  D -->|role| E[依頼文を 8 層で組み立て<br>VM に置く]
  E --> F[VM で claude -p --model …]
  F --> G{outputs が全部ある?}
  D -->|code| H[Mac 側 kit/steps/*.sh を<br>env 付きで実行]
  H --> G2{rc == 0?}
  G -->|yes| T[transition]
  G -->|no| T
  G2 --> T
  T --> S[state.json に history を追記]
  S --> D
  D -->|end / human| R[human なら wip ブランチに退避]
  R --> X[artifact 回収 → sandbox release]
```

| 場面 | runner がすること |
|---|---|
| 起動 | `kit/workflows/<wf>.yml` と PJ の `project.yml`（`workspace/projects/<pj>/` → `examples/projects/<pj>/`）を読み、schema 検証。ブランチ名 `sandbox/<id>-<wf>-<slug>`。`workspace/runs/<日付>-<pj>-<id>/` を作る（既にあれば `-attemptN` に退避） |
| take | `sandbox take <pj> <id>`。VM 内で base を fetch し作業ブランチを切る。merge-pr なら PR の head を checkout |
| agent step | 依頼文を組み立てて `workspace/runs/` に残し、VM の `/home/dev/prompt.md` に置き、`cd $SANDBOX_APP_DIR && timeout <N>m claude -p "$(cat prompt.md)" --model <model>` を実行。終わったら `outputs` のファイルが全部あるかで合否 |
| code step | `kit/steps/<script>` を Mac で実行。env で `PJ TASK RUN_DIR PROJECT_DIR GATES WORK APP_DIR BASE BRANCH WORKFLOW TITLE PR_NUMBER KNOWN_RED` を渡す。rc で合否 |
| transition | `next` / `on_pass` / `on_fail` を見る。`goto` のループ回数を `state.json` の `loops` に数え、`max_loops` を超えたら `else` |
| 差し戻し | 前回の結果（ゲートのログ、review の内容）を「前回の結果（直すこと）」として次の依頼文に添える。base で既に赤なら「直さず報告」と明記 |
| 終了 | `human` なら `origin/sandbox/<id>-<wf>-wip` に push して成果を退避。`~/work/<id>/` を `workspace/runs/…/work/` に回収。`sandbox release` |
| code step 前 | GitHub App トークンを払い出し直す（1 時間失効の対策） |

## 依頼文の 8 層

agent が受け取る依頼文は、runner が毎回この順で組み立てます。結果は `workspace/runs/<run>/prompt-<step>-<n>.md` に残ります。

```mermaid
flowchart TB
  L1[1. 共通の約束<br>roles/_common.md] --> L2[2. 役割の憲法<br>roles/role.md] --> L3[3. step の追加指示<br>workflow yml の brief] --> L4[4. プロジェクト<br>project.yml の repo / stack / cwd / facts / forbidden / review_points] --> L5[5. チケット本文] --> L6[6. 入力 artifact<br>前 step の plan.md など。reviewer には差分] --> L7[7. 前回の結果<br>差し戻しのときだけ] --> L8[8. 出力先の指定<br>~/work/id/plan.md に書け、コミットせよ]
```

| 層 | 出どころ | 変える頻度 |
|---|---|---|
| 1 | `workflow/kit/roles/_common.md` | 低。全役割に効く |
| 2 | `workflow/kit/roles/<role>.md` | 低 |
| 3 | `workflow/kit/workflows/<wf>.yml` の `brief` | 低 |
| 4 | `workspace/projects/<pj>/project.yml` | 中。PJ の状況で |
| 5 | `workspace/kanban/tickets/<id>-….md` | 毎回 |
| 6 | VM の `~/work/<id>/` と `git diff origin/<base>...HEAD` | 毎回 |
| 7 | 直前の step の結果 | 差し戻し時 |
| 8 | workflow yml の `outputs` | 低 |

## モデルの決まり方

役割ごとに既定のクラスがあり、クラスは `workflow/kit/routes.env` でモデル名に解決されます。

| 役割 | 既定クラス | モデル |
|---|---|---|
| planner / reviewer | judgment | `claude-fable-5-1` |
| researcher | research | `claude-sonnet-5` |
| implementer | coding | `claude-opus-5` |

上書きは 3 段。step の `model_class` > 環境変数 `CLAUDE_MODEL`（1 回限り） > 既定。判断は Fable、Web 調査は Sonnet、実装は Opus という型はメンテナの判断（2026-09-06）です。`routes.env` を変えれば別のモデルにできます。

## code step の 3 本

| スクリプト | 何をする | 失敗の条件 |
|---|---|---|
| `gates.sh` | PJ の `gates.sh` を VM に scp して実行。`known_red_gates` の FAIL を INFO に格下げ。結果を `~/work/<id>/gates.txt` に | FAIL が 1 つでもある |
| `pr-create.sh` | コミットがあるか確認 → push → artifact を本文にして `gh pr create` → URL を `~/work/<id>/pr_url` に | コミットが無い、push 失敗 |
| `pr-merge.sh` | コンフリクトマーカーの残りを検査 → base 取り込み済みか確認 → head へ push → ゲート・レビューの結果を PR コメントに → `gh pr merge` | マーカー残り、base 未取り込み、マージ失敗 |
