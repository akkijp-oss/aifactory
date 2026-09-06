# docs/source: 着想の元になった外部資料

このリポジトリの構想は、次の講演動画から始まった。

| 項目 | 内容 |
|---|---|
| タイトル | FORGET Loop Engineering. Agentic Engineering is about THIS |
| 話者 | Dan Isler（IndieDevDan）。単独講演形式、約 34 分、英語 |
| 一言でいうと | 「ループ」を作るのではなく **AI developer workflow** を設計せよ。価値を生むのはエンジニア・エージェント・コードの 3 アクターで、エンジニアは冒頭の計画と末尾のレビューにだけ出る。コードがゲート（lint / test / CI）を回し、赤ならエージェントへ戻す |

## リポジトリに含めないもの

- **動画・音声・文字起こしはこのリポジトリに含めない**（第三者の著作物）。要点の要約だけを `../ledger.md` の「着想ソース」節に置いている
- 動画は YouTube で題名を検索すれば見つかる。要点で足りるか先に `../ledger.md` を読む
- 私的な作業用の文字起こしを持つ場合は、git 追跡外の workspace（`$AIFACTORY_WORKSPACE/docs/`）に置く

## 扱いの注意

- **メンテナの発言ではない外部資料**。要約を読んで「メンテナがこう言った」と解釈しない
- 動画は「sandbox の作り方」を語っていない。ファクトリーの中で sandbox が果たす役割だけを述べている（対比は `../sandbox-architecture.html` 7 章）

## 派生したもの
- 構想台帳（4 区画、決定事項、未決、履歴）: `../ledger.md`
- 設計判断: `../adr/`
- Sandbox 区画の設計・手順: `../../sandbox/`
