# workflow の選び方

このページで分かること: 6 つの workflow の流れ、使いどころ、どの役割とモデルが動くか、差し戻しの上限。

## 一覧

| workflow | 流れ | 使いどころ | 出口 |
|---|---|---|---|
| **hotfix** | plan → implement → gates → review → pr | 本番障害の最小修正。宛先は `hotfix_base`（通常 `main`） | PR → 人間 |
| **bug** | plan → implement（再現テスト先行）→ gates → review → pr | 不具合修正 | PR → 人間 |
| **feature** | research → design → implement → gates → review → pr | 機能追加。調査と設計を挟む | PR → 人間 |
| **chore** | implement → gates → pr | docs・依存更新・設定など判断の要らない雑務。計画もレビューも無し | PR → 人間 |
| **research** | research → judge → end | 調べるだけ。コードは変えない。`summary.md` を回収 | 完了（PR 無し） |
| **merge-pr** | resolve → gates → review → merge | 既存 PR のコンフリクト解消とマージ。本文の `pr: N` で対象を指定 | マージ済み |

## 流れの図

```mermaid
flowchart LR
  subgraph feature
    r1[research<br>Sonnet] --> d1[design<br>Fable] --> i1[implement<br>Opus] --> g1[gates<br>code] --> v1[review<br>Fable] --> p1[pr<br>code] --> h1((human))
    g1 -. 赤 ×2 .-> i1
    v1 -. FAIL ×1 .-> i1
  end
```

```mermaid
flowchart LR
  subgraph bug / hotfix
    pl[plan<br>Fable] --> im[implement<br>Opus] --> ga[gates<br>code] --> re[review<br>Fable] --> pr[pr<br>code] --> hu((human))
    ga -. 赤 ×2 .-> im
    re -. FAIL ×1 .-> im
  end
```

```mermaid
flowchart LR
  subgraph chore
    ic[implement<br>Opus] --> gc[gates<br>code] --> pc[pr<br>code] --> hc((human))
    gc -. 赤 ×2 .-> ic
  end
  subgraph research
    rr[research<br>Sonnet] --> jj[judge<br>Fable] --> ee((end))
  end
  subgraph merge-pr
    rs[resolve<br>Opus] --> gm[gates<br>code] --> rv[review<br>Fable] --> mg[merge<br>code] --> en((end))
    gm -. 赤 ×2 .-> rs
    rv -. FAIL ×1 .-> rs
  end
```

青い箱が agent step（VM 内で `claude -p`）、`code` が code step（Mac 側スクリプトが VM で実行）。点線は差し戻しで、上限を超えると `human` に抜けます。

## 選び方の目安

```mermaid
flowchart TD
  Q1{コードを変えるか} -->|いいえ| RS[research]
  Q1 -->|はい| Q2{既存 PR のマージか}
  Q2 -->|はい| MP[merge-pr]
  Q2 -->|いいえ| Q3{本番が止まっているか}
  Q3 -->|はい| HF[hotfix]
  Q3 -->|いいえ| Q4{判断が要るか}
  Q4 -->|いいえ: docs・依存・設定| CH[chore]
  Q4 -->|はい| Q5{不具合か機能か}
  Q5 -->|不具合| BG[bug]
  Q5 -->|機能| FT[feature]
```

intake はこの目安で判定します。迷ったら **bug**（計画とレビューがある）を選ぶのが安全で、chore は「間違えても影響が小さいもの」に限ります。

## 役割とモデル

| 役割 | クラス | モデル（`routes.env`） | 出力 |
|---|---|---|---|
| planner | judgment | Fable 5.1 | `plan.md` |
| researcher | research | Sonnet 5 | `research.md` / `summary.md` |
| implementer | coding | Opus 5 | git コミット + `report.md` |
| reviewer | judgment | Fable 5.1 | `review.md` |

step 単位で `model_class` を書けば上書きできます。1 回だけ変えるなら環境変数 `CLAUDE_MODEL`。

## 実績と時間の目安

メンテナの環境での実測（2026-09）です。

| run | workflow | agent | ゲート | 結果 |
|---|---|---|---|---|
| Node monorepo の依存更新（差分 3 行） | bug 相当 | 455 秒 | 6 分 | PR |
| Next.js の時間依存テストの修正（kumitate） | bug 相当 | 582 秒 | 16 分 | PR、6 ゲート全緑 |
| Node PJ の CI ゲートの調査 | research | 232 秒 | 無し | summary.md |
| Rails + MySQL PJ の既存 PR のマージ | merge-pr | 解消 16 分 + レビュー 2 分 | 57 分 | マージ済み |
| kumitate の CI の調査 | research | 433 秒 | 無し | summary.md |

時間の大半はゲートです。agent は数分で終わります。

## 自分で workflow を足す

`workflow/kit/workflows/<name>.yml` を 1 枚足せば、`kb new` の `kind` として使えるようになります。形は [workflow yml](../reference/workflow-yml.md)。step の担い手は既存の 4 役割か code step（`gates.sh` / `pr-create.sh` / `pr-merge.sh`）で、新しい役割が要るなら `workflow/kit/roles/<role>.md` を足します。
