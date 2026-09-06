# 0006 Claude / GitHub トークンは PJ ごとに持ち、貸出中の VM にも差し替えを反映できるようにする

日付: 2026-09-06 / 状態: 採用（0005 を拡張。0005 の「take 時に tmpfs へ注入」は変えない）

## 状況
0005 は `~/.config/sandbox/env` の1箇所に `CLAUDE_CODE_OAUTH_TOKEN` を置く前提だった。メンテナの判断（2026-09-06）:
- トークンは **環境（PJ）ごとに変えられる**ようにしたい（PJ ごとに別アカウント・別トークンを使う想定。Mac には複数の Claude プロファイルが既にある）
- **希望した時にキーを変更できる**ようにしたい（有効期限切れ・アカウント切替・失効時に、貸出中の VM を作り直さずに差し替えたい）

## 決定
1. 設定を3層にする。後の層が前の層を上書きする
   - `~/.config/sandbox/env` … 全体の既定（接続先・鍵・SB_JUMP など。トークンを置いてもよい）
   - `~/.config/sandbox/pj/<pj>.env` … PJ 別の上書き（`CLAUDE_CODE_OAUTH_TOKEN`、`GH_TOKEN`、`APP_PORT` など）
   - 実行時の環境変数 … 一回限りの上書き（`SANDBOX_CLAUDE_TOKEN=... sandbox take ...` / `SANDBOX_GH_TOKEN=...`）。**シェルに export されている `CLAUDE_CODE_OAUTH_TOKEN` / `GH_TOKEN` は無視する**（2026-09-06 追記: メンテナのシェルに Claude 用のトークンが export されており、PJ 設定を黙って上書きしていた）
2. CLI に運用補助を足す（区画間の契約=5操作は変えない）
   - `sandbox token set|clear <pj|global> [claude|gh]` … 対話入力（エコー無し）または stdin で保存。ファイルは 600
   - `sandbox token show [pj]` … どの値が効いているかをマスク表示
   - `sandbox reinject <task-id>|--all` … 貸出中の VM の `/run/sandbox/env` を現在の設定で書き直す（巻き戻しなし）
3. `take` / `reset` は task の PJ を `state.json` から引いて PJ 層を読む。PJ 名は `take <pj>` の引数がそのまま鍵

## 理由
- PJ ごとにファイルを分けると、片方のトークンを差し替えても他方に影響しない。誤って別 PJ のアカウントで動くこともない
- 実行時環境変数を最優先にしておくと、上位（workflow / kanban）が task 単位で別トークンを渡す将来にも対応できる
- `reinject` は tmpfs を書き直すだけなので、作業中の VM の状態（ファイル・DB）を壊さない。0005 の「巻き戻しで消える」性質はそのまま

## 結果（トレードオフ）
- VM 内で起動済みの `claude` プロセスは env を読み直さないので、差し替え後は **再起動が必要**（`reinject` がその旨を表示する）
- ファイルが増える分、`token show` で「今どれが効いているか」を必ず確認できるようにした
- `GH_TOKEN` も同じ層で扱う。テンプレート焼き込み時にだけ使い、VM には残さない（0005 と同じ）
