# 結果を読む

このページで分かること: run が終わった後に何がどこに残るか、PR の読み方、失敗の切り分け方。

## 置き場の全体

```mermaid
flowchart LR
  subgraph GitHub
    PR[PR<br>本文に plan / report / review / gates]
  end
  subgraph workspace/kanban/
    DB[(kanban.db<br>状態・履歴)]
    BOARD[BOARD.md]
    T[tickets/id-….md]
  end
  subgraph workspace/runs/日付-pj-id/
    S[state.json]
    P[prompt-*.md]
    A[agent-*.log]
    C[code-*.log]
    W[work/<br>plan.md report.md review.md gates.txt summary.md]
  end
```

## まず見るもの

```bash
kanban/bin/kb show 204          # 状態・PR 番号・run ディレクトリ・メモ
kanban/bin/kb history 204       # 状態遷移の履歴（いつ誰が何を）
cat workspace/kanban/BOARD.md   # 全体のボード（workspace は $AIFACTORY_WORKSPACE）
```

`status` が `review` なら PR を読む番です。`blocked` ならメモに理由（人間へ / runner 異常終了 / project.yml 無し）があります。

ブラウザで見るなら [Web コンソール](console.md)。ボードから run の工程トラック（step ごとの合否と所要）とログ、成果物までたどれます。

## PR の読み方

PR の本文は `kit/steps/pr-create.sh` が組み立てます。上から順に、

1. 「aifactory sandbox による自動作業。workflow: bug。マージは人間が判断する」の定型
2. `plan.md`（planner の計画: 範囲、検証方法、リスク）
3. `report.md`（implementer の報告: 何をした、テストの結果、範囲外の発見）
4. `review.md`（reviewer の判定: PASS / FAIL と根拠、人間向けメモ）
5. `gates.txt`（ゲートの結果: `PASS typecheck` / `FAIL test (~/gates/test.log)`）

レビューの勘所は、reviewer が「範囲」「正しさ」「安全」「PJ 固有」「ゲート」の順に見た結果を `review.md` に残しているので、そこで挙がった懸念を先に確認することです。reviewer は範囲外の懸念を「人間向けメモ」に分けて書く約束です。

## 成果物（work/）

| ファイル | 誰が書く | 内容 |
|---|---|---|
| `plan.md` | planner | 再現条件、原因の仮説、変更範囲（ファイル単位）、検証方法、リスク。危険なら先頭に `STOP` |
| `research.md` | researcher | 結論（3 行）、項目ごとの所見（出典つき）、不明点、planner への申し送り |
| `report.md` | implementer | 何を変えたか、走らせたテストと結果、範囲外の発見、判断に迷った点 |
| `review.md` | reviewer | PASS / FAIL、根拠、直すべき点、人間向けメモ |
| `gates.txt` | gates.sh | ゲートごとの PASS / FAIL / INFO と ログの場所 |
| `summary.md` | judge（research workflow） | 調査の結論と次にやるべきこと |

## 失敗の切り分け

```mermaid
flowchart TD
  X{state.json の result} -->|end| OK[成功。PR か summary]
  X -->|human| H{history の最後の step}
  H -->|gates が FAIL ×3| G[code-gates-*.log と work/gates.txt<br>base で既に赤なら known_red_gates]
  H -->|review が FAIL ×2| R[work/review.md の指摘<br>チケットの範囲が曖昧でないか]
  H -->|agent が出力を書かなかった| A[agent-*.log の末尾<br>認証・timeout・ツール拒否]
  H -->|pr / merge が失敗| P[code-pr-*.log<br>トークン失効・コミット無し・コンフリクトマーカー]
  X -->|無い / 途中| V[runner が落ちた<br>ssh 切断・別セッションの VM 作り替え]
```

| 症状 | 読む場所 | よくある原因 |
|---|---|---|
| gates が赤で `human` | `code-gates-<n>.log`、VM 内 `~/gates/<name>.log`（回収後は `work/`） | base で既に赤（`known_red_gates` に書く）、環境依存のテスト、依存の未インストール |
| review が FAIL | `work/review.md` | 範囲外の変更、テストを弱めて緑にした、migration の後方互換 |
| agent が artifact を書かず step 失敗 | `agent-<step>-<n>.log` の末尾 | トークン失効、`timeout_min` 超過、依頼文の出力先指定を見落とし |
| `pr-create.sh` が「コミットが無い」 | `code-pr-<n>.log` | implementer がコミットしなかった。`report.md` に理由があるはず |
| merge が失敗 | `code-merge-<n>.log` | GitHub App トークンの失効（1 時間）、コンフリクトマーカー残り、base 未取り込み |
| ssh timeout で途中終了 | ターミナル出力 | 別セッションが VM を再起動した。`kb reopen` → `kb run` |

## 依頼文を読む

「agent はなぜそうしたか」は `prompt-<step>-<n>.md` を読むと分かります。8 層（共通の約束 → 役割の憲法 → step の brief → PJ の事実・禁止・観点 → チケット → 入力 artifact → 前回の結果 → 出力先）で組み立てられているので、足りない事実や誤った禁止事項があればそこで見つかります。直す先は [設定の出どころ](../concepts/configuration.md)。

## 記録を残す

実行記録（`workspace/runs/`）と台帳（`workspace/kanban/`）はリポジトリの外（workspace）にあり、git には入りません。残したいなら workspace ごとバックアップするか、workspace を別の（private な）リポジトリにします。ログには秘密情報が出ないよう、agent は「秘密をログに出さない」約束で動き、`sandbox` CLI はトークンをマスク表示しますが、workspace を共有する前には `agent-*.log` に実値が混ざっていないかを確かめてください。
