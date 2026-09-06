# workflow yml

`workflow/kit/workflows/<name>.yml`。チケット種別ごとの手順。step の並びと分岐だけを書き、文章（役割の憲法）は `roles/*.md` を参照する。形は `workflow/kit/schema/workflow.schema.json` で検証される。

## トップレベル

| 項目 | 型 | 必須 | 意味 |
|---|---|---|---|
| `name` | string（`^[a-z][a-z0-9-]*$`） | ✅ | workflow 名。`kb new` の `kind` に使う |
| `description` | string | ✅ | 1 行の説明。intake が種別一覧に出す |
| `base_branch` | string | | 作業ブランチの元と PR の宛先。省略時は `project.yml` の `base_branch`。`hotfix_base` と書くと `project.yml` の `hotfix_base` |
| `inputs` | array | | workflow 全体の入力 artifact 名。v1 は `[ticket.md]` 固定 |
| `start` | `base` / `pr` | | 作業ブランチの作り方。`base` = base から新規ブランチ（既定）。`pr` = チケット 2 行目の `pr: N` の PR head を checkout（base はその PR の base） |
| `steps` | array | ✅ | step の並び（1 つ以上） |

## step

| 項目 | 型 | 意味 |
|---|---|---|
| `id` | string（`^[a-z][a-z0-9_-]*$`） | step の名前。transition の行き先に使う。必須 |
| `role` | planner / implementer / reviewer / researcher | agent step。憲法は `roles/<role>.md`、モデルは役割の既定クラス |
| `code` | string | code step。`kit/steps/` からの相対パス。Mac で実行され、VM へは `sandbox ssh` で入る |
| `brief` | string（Markdown） | この step の追加指示。役割の憲法の後ろに付く |
| `model_class` | judgment / research / coding | 役割の既定クラスを上書き |
| `inputs` | array of artifact | VM の `~/work/<id>/` から読んで依頼文に添える。reviewer には自動で差分も付く |
| `outputs` | array of artifact | step が作るべき artifact。agent step では依頼文に「ここに書け」と出る。無ければ失敗 |
| `next` | transition | 結果に関係なく次へ。分岐の無い step が失敗したら human |
| `on_pass` / `on_fail` | transition | 合否で分岐 |
| `timeout_min` | integer | agent の上限時間（既定 60） |

`role` と `code` はどちらか 1 つ（両方は schema エラー）。

### artifact

`~/work/<id>/` 配下の相対パス（`plan.md`、`gates.txt`）。特別な値が 2 つ。

| 値 | 意味 |
|---|---|
| `git` | outputs にあれば「作業ブランチにコミットせよ」。ファイルとしては回収しない |
| `pr_url` | code step が PR の URL を `~/work/<id>/pr_url` に置く。runner が `state.json` の `pr_url` に写す |

### transition

文字列か、オブジェクト。

| 形 | 意味 |
|---|---|
| `"implement"` | その step へ |
| `"end"` | 正常終了 |
| `"human"` | 人間に渡して終了。成果は wip ブランチに退避 |
| `{ goto: implement, max_loops: 2, else: human }` | `implement` へ戻る。この遷移で戻れるのは 2 回まで。超えたら `human` |

`max_loops` の既定は 1、`else` の既定は `human`。回数は `state.json` の `loops` に `"<from>-><to>": n` で数える。

## 例

=== "bug.yml"

    ```yaml
    name: bug
    description: 不具合修正。計画 → 再現テスト → 修正 → ゲート → レビュー → PR
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
        on_pass: pr
        on_fail: { goto: implement, max_loops: 1, else: human }
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
- PJ 固有のことを書かない（`project.yml` へ）
- 新しい役割を使うなら `roles/<role>.md` と runner の既定クラス対応を足す
- 変更は次の run から効く。走っている run には効かない
- `kb run <id> --dry-run` で schema 検証と依頼文の組み立てを確かめられる
