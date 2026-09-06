# ワークフローの YAML

`workflow/kit/workflows/<name>.yml` は、チケットの種別ごとに工程の順序と分岐を定義するファイルです。各役割の行動ルールは `roles/*.md` に記述します。定義の形式は `workflow/kit/schema/workflow.schema.json` で検証されます。

## トップレベル

| 項目 | 型 | 必須 | 意味 |
|---|---|---|---|
| `name` | string（`^[a-z][a-z0-9-]*$`） | ✅ | ワークフロー名。`kb new` の `kind` に使う |
| `description` | string | ✅ | 1 行の説明。intake が種別一覧に出す |
| `base_branch` | string | | 作業ブランチの元と PR の宛先。省略時は `project.yml` の `base_branch`。`hotfix_base` と書くと `project.yml` の `hotfix_base` |
| `inputs` | array | | ワークフロー全体の入力成果物名。v1 は `[ticket.md]` 固定 |
| `start` | `base` / `pr` | | 作業ブランチの作り方。`base` = base から新規ブランチ（既定）。`pr` = チケット 2 行目の `pr: N` の PR head を checkout（base はその PR の base） |
| `steps` | array | ✅ | 工程の並び（1 つ以上） |

## 工程

| 項目 | 型 | 意味 |
|---|---|---|
| `id` | string（`^[a-z][a-z0-9_-]*$`） | 工程の名前。transition の行き先に使う。必須 |
| `role` | planner / implementer / reviewer / researcher | エージェントが担当する工程。行動ルールは `roles/<role>.md`、モデルは役割の既定クラス |
| `code` | string | スクリプトが担当する工程。`kit/steps/` からの相対パス。制御系で実行され、VM へは `sandbox ssh` で入る。特別値 `sync-base`（PR 直前の base 取り込み）は runner 内蔵で、`kit/steps/` にファイルは無い |
| `brief` | string（Markdown） | この工程の追加指示。役割ごとの行動ルールの後ろに付く |
| `model_class` | judgment / research / coding | 役割の既定クラスを上書き（同じクラスを使う工程は `routes.env` の 1 行で一緒に動く） |
| `model` | string（`^[A-Za-z0-9._-]{1,64}$`） | この工程だけモデル名を固定する。`routes.env` より優先し、`CLAUDE_MODEL` はさらに優先。同じクラスの他の工程は動かない |
| `inputs` | array of 成果物 | VM の `~/work/<id>/` から読んで依頼文に添える。reviewer には自動で差分も付く |
| `outputs` | array of 成果物 | 工程が作るべき成果物。エージェントが担当する工程では依頼文に「ここに書け」と出る。なければ失敗 |
| `next` | transition | 結果に関係なく次へ。分岐のない工程が失敗したら human |
| `on_pass` / `on_fail` | transition | 合否で分岐 |
| `timeout_min` | integer | エージェントの上限時間（分。既定 60）。超えると `timeout` が工程を切り、runner が追跡済みの未コミット変更を `wip: step timeout` としてコミットしてから失敗にします（退避ブランチに残ります） |

`role` と `code` は、どちらか一方を指定します。両方を書くとスキーマ検証でエラーになります。

### 成果物

成果物は、`~/work/<id>/` を基準とした相対パスで指定します。たとえば `plan.md` や `gates.txt` です。次の 2 つは特別な値として扱われます。

| 値 | 意味 |
|---|---|
| `git` | outputs にあれば「作業ブランチにコミットせよ」。ファイルとしては回収しない |
| `pr_url` | スクリプトが担当する工程が PR の URL を `~/work/<id>/pr_url` に置く。runner が `state.json` の `pr_url` に写す |

### transition

遷移先の工程名を文字列で指定するか、回数の上限などを含むオブジェクトで指定します。

| 形 | 意味 |
|---|---|
| `"implement"` | その工程へ |
| `"end"` | 正常終了 |
| `"human"` | 人間に渡して終了。成果は wip ブランチに退避 |
| `{ goto: implement, max_loops: 2, else: human }` | `implement` へ戻る。この遷移で戻れるのは 2 回まで。超えたら `human` |

`max_loops` の既定値は 1、`else` の既定値は `human` です。やり直した回数は、`state.json` の `loops` に `"<from>-><to>": n` の形式で記録します。

!!! note "軽微なレビュー指摘は 1 周だけ延びる"

    `role: reviewer` の工程の FAIL に限り、`review.md` に `severity: minor` の 1 行があると、`max_loops` を使い切っていても **もう 1 周だけ** `goto` に戻ります。加点は 1 遷移につき 1 回きりで（使ったことは `state.json` の `severity_bonus` に `"<from>-><to>": 1` で残ります）、`severity: major` と無指定は従来どおり `else`（既定は `human`）です。`max_loops` の値そのものは変わりません（ADR-0053）。

## 例

=== "bug.yml"

    ```yaml
    name: bug
    description: 不具合修正。計画 → 再現テスト → 修正 → ゲート → レビュー → base 取り込み → PR
    inputs: [ticket.md]
    steps:
      - id: plan
        role: planner
        brief: |
          再現条件を最初に確定させる。再現テストをどこに（どのテストファイルに）書くかまで計画に含める。
        outputs: [plan.md]
        next: implement
      - id: implement
        role: implementer
        brief: |
          **先に再現テストを書いて赤を確認**してから直す。再現テストが書けない場合は理由を report.md に書き、代わりの検証を示す。
        inputs: [plan.md]
        outputs: [git, report.md]
        next: gates
      - id: gates
        code: gates.sh
        outputs: [gates.txt]
        on_pass: review
        on_fail: { goto: implement, max_loops: 2, else: human }
      - id: review
        role: reviewer
        inputs: [plan.md, report.md, gates.txt]
        outputs: [review.md]
        on_pass: sync
        on_fail: { goto: implement, max_loops: 1, else: human }
      - id: sync
        code: sync-base
        on_pass: pr
        on_fail: { goto: resolve, max_loops: 2, else: human }
      - id: resolve
        role: implementer
        brief: |
          **base の取り込みで戻された**。戻された理由の解消だけを行い、PR 本来の変更は直さない・広げない。
        outputs: [git, report.md]
        next: gates
      - id: pr
        code: pr-create.sh
        inputs: [plan.md, report.md, review.md, gates.txt]
        outputs: [pr_url]
        next: human
    ```

=== "research.yml"

    ```yaml
    name: research
    description: 調査。researcher が一次情報を集めて research.md にまとめる。planner が要点と次の一手を判断して summary.md を書く
    inputs: [ticket.md]
    steps:
      - id: research
        role: researcher
        outputs: [research.md]
        next: judge
      - id: judge
        role: planner
        brief: |
          research.md を読み、問いに対する答えと「次にやるべきこと（やらないという判断も含む）」を summary.md に書く。
          コードや計画は書かない。人間が 2 分で読める長さにする。
        inputs: [research.md]
        outputs: [summary.md]
        next: end
    ```

=== "merge-pr.yml（要点）"

    ```yaml
    name: merge-pr
    description: 既存 PR のコンフリクトを解消し、ゲート・レビューを通して base へマージする
    start: pr                       # チケットの pr: N の head を checkout
    steps:
      - id: resolve
        role: implementer
        outputs: [git, report.md]
        next: gates
      - id: gates
        code: gates.sh
        on_pass: review
        on_fail: { goto: resolve, max_loops: 2, else: human }
      - id: review
        role: reviewer
        on_pass: merge
        on_fail: { goto: resolve, max_loops: 1, else: human }
      - id: merge
        code: pr-merge.sh
        on_pass: end
        on_fail: human
    ```

## 書き方の注意

- YAML の平文にバッククォートや `: ` を含める項目は `"..."` で囲む（PyYAML が誤読する）。`brief` は `|` のブロックにする
- プロジェクト固有のことを書かない（`project.yml` へ）
- 新しい役割を使うなら `roles/<role>.md` と runner の既定クラス対応を足す
- 変更は次の run から効く。実行中の run には効かない
- `kb run <id> --dry-run` でスキーマ検証と依頼文の組み立てを確かめられる
