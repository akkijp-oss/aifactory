# 役割とモデル

`workflow/kit/roles/*.md`（役割の憲法）と `workflow/kit/routes.env`（クラス → モデル）。

## 役割

| 役割 | クラス | やること | やらないこと | 出力 |
|---|---|---|---|---|
| **planner** | judgment | チケットとリポジトリを読み、再現条件・原因の仮説・変更範囲（ファイル単位）・検証方法・リスクを決める。不明確・矛盾・危険なら先頭に **STOP** | コードを書かない | `plan.md` |
| **implementer** | coding | 計画に従って実装。バグ修正は先に失敗するテストを書いて赤を確認。lint / 型 / 関係するテストを自分で緑にしてコミット | 範囲外に手を出さない（止めて report.md に理由）。push しない | git コミット + `report.md` |
| **researcher** | research | 問いを 3 つ以内に分解し、リポジトリと Web の一次情報を集め、出典つきで整理。GitHub は `gh`（CI 履歴は `gh run list`） | コードの変更、コミット。推測を事実のように書かない | `research.md`（research workflow では judge が `summary.md`） |
| **reviewer** | judgment | 差分・計画・報告・ゲート結果を「範囲 → 正しさ → 安全 → PJ 固有 → ゲート」の順に見て PASS / FAIL。範囲外の懸念は人間向けメモに分ける | コードを直さない | `review.md` |

すべての役割に `_common.md`（共通の約束）が先に付きます。

## 共通の約束（`_common.md`）

- 作業ブランチで行う。`main` / `develop` に直接コミットしない、ブランチを切り替えない
- **push しない**。push と PR はコード（runner）がやる
- コミットは自分で。メッセージは日本語で「何を・なぜ」を 1 行目に
- 追跡外のファイルを `git add` しない。`git add -A` を使わない
- 秘密情報をファイルに書かない、ログに出さない
- 範囲の外を変えない。範囲外の問題は直さずに報告書に書く
- 指示と実態が食い違ったら実態を正として、食い違いを報告書に書く
- 推測で埋めない。判断が必要なら選択肢と推奨を書き、安全側で進める
- 指定された artifact は必ず `~/work/<id>/` の指定パスに書く（無いと step は失敗扱い）

## 出力の形

=== "plan.md"

    ```
    # 計画: <題名>
    ## 再現条件と原因の仮説（確度つき）
    ## 変更範囲（ファイル単位。触らない範囲も）
    ## 検証方法
    ## リスクと判断基準
    ```
    危険なら先頭に `STOP` と理由と人間への質問。

=== "report.md"

    ```
    # 報告: <題名>
    ## 変えたもの
    ## 走らせたテストと結果
    ## 範囲外で見つけたこと（直していない）
    ## 判断に迷った点
    ```

=== "research.md"

    ```
    # 調査: <問い>
    ## 結論（3 行以内。事実だけ）
    ## 項目ごとの所見（出典つき）
    ## 不明・要確認
    ## planner への申し送り
    ```

=== "review.md"

    ```
    # レビュー: <題名>
    ## 判定: PASS / FAIL
    ## 根拠
    ## 直すべき点（FAIL のとき）
    ## 人間向けメモ（範囲外の懸念）
    ```

## routes.env

```
MODEL_judgment=claude-fable-5-1
MODEL_research=claude-sonnet-5
MODEL_coding=claude-opus-5
MODEL_default=claude-opus-5
```

| クラス | 用途 | 既定の役割 |
|---|---|---|
| judgment | 最重要判断 | planner、reviewer、（research workflow の）judge、intake |
| research | Web クローリング調査 | researcher |
| coding | コーディング | implementer |

判断は Fable、Web 調査は Sonnet、それ以外は Opus という型はメンテナの判断（2026-09-06）。`routes.env` を変えれば別のモデルにできる。

## 上書き

| 範囲 | 方法 |
|---|---|
| 全体 | `routes.env` を変える |
| 1 つの step | workflow yml の `model_class: judgment` など |
| 1 回の run | 環境変数 `CLAUDE_MODEL=claude-opus-5 kb run 204` |

## 役割を足す

1. `workflow/kit/roles/<role>.md` を書く（クラス、やること、禁止、出力の形）
2. runner の役割 → 既定クラスの対応（`bin/run` の `model_for`）に足す
3. workflow yml の step で `role: <role>` として使う
