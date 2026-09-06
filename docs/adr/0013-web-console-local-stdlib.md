# ADR-0013: Web コンソールは Mac ローカルの Python 標準ライブラリ製。状態は既存 CLI 経由でしか変えない

日付: 2026-09-06 / 状態: 採用 / 決定者: メンテナ（「このプロジェクトの、Web コンソールが欲しい」）

## 状況
- 4 区画が揃い、状態は `kanban.db`（チケット）・`$AIFACTORY_WORKSPACE/runs/*/state.json`（run）・`~/.config/sandbox/state.json`（貸出）に散った。`kb list` / `ls $AIFACTORY_WORKSPACE/runs` / `sandbox ls` と 3 つの CLI を順に叩かないと現在地が分からなかった
- 1 run が 60〜80 分かかり、その間に「今どの step か」「ゲートの出力」を見るには `runs/` のログを手で開く必要があった
- メンテナは cmux の別 surface で複数の AI セッションを同時に走らせている。コンソールが状態を独自に持つと、CLI と食い違う

## 決定
- `console/bin/console`（Python 3 標準ライブラリの `http.server`）+ `console/static/`（素の HTML/CSS/JS）。依存ゼロ、ビルド無し、127.0.0.1 専用、認証無し
- **読む**: `kanban.db` は読み取り専用（`mode=ro`）、run は `state.json` とファイル、貸出は `state.json`。読めるファイルの根を列挙して限る
- **動かす**: `kb` / `glue/bin/intake` / `glue/bin/dispatch` / `sandbox` を **そのまま** 子プロセスで呼ぶ。数秒で終わるものは同期、長いものは `console/jobs/<id>/` に出力を流すジョブにする。コンソールが DB や state.json に直接書くことはしない
- 二重起動（同じチケットの run、2 本目の dispatch）はジョブ起動のロックの中で弾く
- POST は `X-Console: 1` ヘッダを要求する

## 理由
- 「状態を変えるのは kb」というルート README の約束を、UI にも適用する。UI が独自の書き込み経路を持つと、同時セッションとの衝突点が増える
- 標準ライブラリだけなら、runner や kb と同じ python3 で動き、環境の罠（node のネイティブ依存・pip の版差）を持ち込まない。**コストより品質**の方針では「壊れない」が優先
- ローカル専用にすることで、認証・TLS・トークン露出の設計を丸ごと避けられる。tailnet や外に出したくなったら、その時に別 ADR で決める
- ジョブを切り離すのは、80 分の run を HTTP の応答で待たせず、ブラウザを閉じても run が続くようにするため

## 結果（トレードオフ）
- 良い: 起動 1 コマンドで、ボード・工程トラック・ログ・貸出が 1 画面。起票 → 配車 → 追い読み → 状態確認がブラウザで完結する
- 悪い: step の中（VM 内で走っている agent の出力）はリアルタイムでは見えない。runner が step 終了時にしかログを書かないため。runner のストリーム化が先
- 悪い: 認証が無いので Mac の外には出せない。共有したくなったら別 ADR
- 悪い: ジョブの記録はローカルの `console/jobs/`（git 追跡外）。正本ではなく、正本は `$AIFACTORY_WORKSPACE/runs` と `kb history`
- 実測（2026-09-06）: 複製 DB（`KB_ROOT`）で状態遷移・起票・`kb run --dry-run`・停止・再起動後の復元を確認。実機の `sandbox ls` が数秒で返る。踏んだ罠: 子プロセスの PATH 先頭に `/usr/local/bin` を足すと python3 が pyenv のものでなくなり runner の `yaml` が見つからない → 末尾に足す形に修正
