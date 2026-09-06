# ADR-0014: agent step の出力は stream-json を人が読める形に起こして逐次書き、生イベントはローカルにだけ残す

日付: 2026-09-06 / 状態: 採用 / 決定者: メンテナ（「残っている所を進めてほしい。コストよりも品質を求めます」）

## 状況
- runner v1 の agent step は `claude -p --output-format text` の標準出力を **終了後に一括で** `agent-<step>-<n>.log` に書いていた。implement や gates が 60 分かかる間、何をしているかは VM に ssh しないと分からない
- Web コンソール（ADR-0013）を作ったが、step の途中は runner がログを書かないので「追い読み」する対象が無かった
- `claude -p` は `--output-format stream-json --verbose` で 1 行 1 イベント（`system` / `assistant` / `user` / `result`）を逐次出す。ツール呼び出しとその結果、最終報告、費用が JSON で取れる

## 決定
- agent step は `--output-format stream-json --verbose` で呼び、runner が **1 行ずつ受けて**:
  - 人が読める形（`[+MM:SS]` の経過、assistant の文、`▶ ツール名: 引数`、`↳ 結果の先頭 3 行`、`result: … turns / duration / cost`）を `agent-<step>-<n>.log` に逐次書く（記録として残す）
  - 生の JSON 行を `agent-<step>-<n>.jsonl` に書く（デバッグ用。Mac のローカルにだけ残す）
  - （2026-09-06 追記: 公開化に伴い run ディレクトリ全体を `$AIFACTORY_WORKSPACE/runs/` に移し git 追跡外にした。起票時は `.log` を git 追跡、`.jsonl` を `.gitignore` で除外していた。「人が読む形」と「生」の区別はそのまま）
- code step（gates / pr / merge）も同じ仕組みで `code-<step>-<n>.log` に逐次書く
- `state.json` に `current: {step, kind, log, since}` を置き、step が終わったら `null` にする。コンソールはこれを見て「今の step のログ」を自動で開く
- 実装は runner 内の `stream()`（Popen で行ごとに書いて flush）と `EventRenderer`（JSON 行 → 文章。JSON でない行はそのまま通す）

## 理由
- 60 分の step の途中が見えることは、失敗の早期発見と「agent が何をしているか」の把握に直結する。**コストより品質**の方針で、待つだけの時間を減らす
- text 出力では最終報告しか残らなかった。ツール呼び出しの列が残ると、後からの切り分け（どのファイルを読んで何を実行したか）が格段に楽になる
- 生 JSONL は 1 step で数十 KB〜数 MB（ツール結果に読んだファイルの中身が丸ごと入る）。git に入れると `runs/` が膨らむ。人が読む形の `.log` があれば git 上の記録として足り、生は Mac に残っていれば再現・デバッグに使える
- `current` を state.json に持たせるのは、ログ名の規則（`<kind>-<step>-<n>`）をコンソール側で再現させないため。正本は runner

## 結果（トレードオフ）
- 良い: コンソールの run 画面とボードに「今どの step で何をしているか」が出る。`tail -f` でも追える。ログに費用（`cost=$0.04`）が残る
- 悪い: ログの形式が変わった。2026-09-06 昼までの run（v1 前半）は text 形式のまま。混在は読む側が日付で判断する
- 悪い: 生イベントは Mac にしか無い。別の Mac に移すときは `.jsonl` を持って行かないと消える（`.log` は残る）
- 悪い: Claude Code の stream-json のイベント形式に依存する。形式が変わると `EventRenderer` が読めない行をそのまま通すので壊れはしないが、読みにくくなる。その時は `EventRenderer` を直す
- 実測（2026-09-06）: Mac 上の偽 VM（`sandbox` / `scp` のシム）で research workflow を 2 周。step の途中で `state.json` の `current` が入り、`.log` が 1 イベントずつ増え、終了時に `.jsonl`（26 KB）が揃うことを確認。Haiku で 1 周 40 秒、費用 $0.08
