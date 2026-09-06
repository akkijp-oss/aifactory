# 0009 workflow の定義は YAML + JSON Schema（文章は Markdown）、手順は常備側に 1 つ、PJ 固有は事実と方針だけ

日付: 2026-09-06 / 状態: 採用

## 状況
sandbox 区画ができ、次に「その中で誰がどの順に呼ばれ、何を受け取り何を出し、データがどこにあるか」を定義するファイル群が要る（メンテナとの相談 2026-09-06）。これがチケット種別（hotfix / bug / feature / chore）ごとの AI developer workflow になる。メンテナの初期案は JSON。sandbox 常備と PJ 固有を分けたい、という要望。

## 決定
1. **形式**: 骨格は YAML、正しさは JSON Schema で検証、文章（役割の憲法・追加指示）は Markdown。機械が受け渡す状態は JSON（`runs/<run>/state.json`）
2. **語彙**: workflow / step / role / artifact / transition の 5 つ（`workflow/README.md`）。step の担い手は role（agent）か code のどちらか一方
3. **置き場**: 手順（workflows）・役割（roles）・code step・経路表は `workflow/kit/`（常備、全 PJ 共通）。PJ 固有は `$AIFACTORY_WORKSPACE/projects/<pj>/project.yml`（repo / base / app_dir / gates / facts / review_points / forbidden）と `gates.sh` に限る（同梱サンプルは `examples/projects/<pj>/`。起票時のパスは `sandbox/templates/<pj>/`）。**手順を PJ ごとに複製しない**
4. **artifact の置き場**: VM の `~/work/<task>/`（規約で固定）。runner が終了時に `$AIFACTORY_WORKSPACE/runs/<run>/work/` へ回収。コードの出口は push（PR）だけ
5. **モデル**: 役割のクラス（judgment / research / coding）→ `kit/routes.env`（Fable / Sonnet / Opus）。step の `model_class` と環境変数 `CLAUDE_MODEL` で上書き可（メンテナの判断: 最重要判断 = Fable、Web 調査 = Sonnet、他 = Opus）
6. **入口**: 当面、音声メモからチケットは作らない。workflow は `ticket.md` から始まる

## 理由
- JSON はコメントと複数行が書けず、日々手で触る定義に向かない。YAML + Schema なら読みやすさと厳密さを両立できる
- 手順を 1 か所にすると「workflow を育てる」ことができる。PJ 側に複製すると改善が PJ 数だけ分散する
- artifact をファイルに固定すると、agent への「ここに書け」と code の「ここから読む」が一律になり、VM が消えても記録が残る
- 役割ごとにモデルを変える経路表を定義の外（routes.env）に置くと、価格や性能の変化に定義を触らず追随できる

## 結果（トレードオフ）
- YAML の平文にバッククォートや `: ` を含めると PyYAML が誤読する。項目は `"..."` で囲む（PJ 定義の project.yml で踏んだ）
- runner v1 は逐次実行のみ（並列 step 無し）。step 間の待ち合わせや並列は必要になったら schema を拡張する
- reviewer の判定は `review.md` 1 行目の `PASS` / `FAIL` で機械的に読む。書式が崩れると FAIL 扱いになる（憲法で強制）
