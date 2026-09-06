# 0001 1リポジトリ（モノレポ）で始める

日付: 2026-09-05 / 状態: 採用

## 状況
4区画（sandbox / kanban / workflow / glue）をどう分けて管理するか。区画ごとにリポジトリを分ける案があった。

## 決定
`akkijp-oss/aifactory` の1リポジトリに、トップレベルディレクトリで区画を分けて置く。

## 理由
- 区画間の契約がまだ見つかっていない。分割は契約を固定する行為で、見つかる前にやると誤った境界が残る
- v0 の実体は bash と Markdown で小さく、区画をまたぐ変更が頻繁。分けるとコミットが分断され経緯が追えない
- sandbox が他の用途（kaizen ループ、cmux-devteam）から使われるようになった時点で切り出せば、境界は自然に決まっている

## 結果
- PJ のリポジトリ名は PJ 定義（`$AIFACTORY_WORKSPACE/projects/<pj>/`、git 追跡外）に入る（起票時は `sandbox/templates/{pj}/` にあり、リポジトリ全体を private にしていた。2026-09-06 に PJ 定義を workspace へ出して公開化。同梱サンプルは `examples/projects/`）
- 切り出し時は `git subtree split` で履歴ごと出せる
