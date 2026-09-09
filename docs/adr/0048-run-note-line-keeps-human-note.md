# ADR 0048: チケットの `note` は先頭の `[run] ` 行だけが機械のもので、残りの行は人のもの

- 状態: Accepted
- 日付: 2026-09-10
- チケット: 342

## 状況

`kb run` / `kb sync` / 再走（`kb run --from`）は、run の結果から状態の要約を作って `update(..., note=...)` で
チケットの `note` を**丸ごと**置き換えていた。人が `kb set --note` で書いた申し送りはそこで消える。

2026-09-09 の #268 で実測: PM が「Mac で実施。Linux sandbox は #250 未マージで gates 赤のため…」と書いた後、
run が provision で落ちて `note` は「VM を取得できず開始前に終了: …」だけになり、resume 後は「人間へ（PR 無し）」、
成功後は「PR 待ち <url>」と続けて置き換わった。#270 でも同じ。

`kb history` に old/new が残るので DB 上は失われていないが、一覧（BOARD.md / console / MCP `ticket_list`）が出すのは
最新の `note` だけなので、人が書いた文脈は毎回見えなくなる。

一方で、前回の run 由来の文（「人間へ（wip: …）…」）を実行中のチケットに残すと一覧が古い状態に見えるので、
run は run で自分の文を最新に保つ必要がある（#294 / ADR-0039）。両方を 1 つの `note` に載せる規則が要る。

## 決定

1. **`note` の 1 行目が `[run] ` で始まるならそれが run 由来の状態行、2 行目以降は人（PM）が書いた行**とする。
   `note` 全体の意味は「状態の要約」で変えない。
2. **run 由来の経路（`apply_result` = `kb run` の末尾と `kb sync`、および再走の `rerun_note`）は自分の 1 行目だけを
   差し替え、人の行はそのまま後ろに残す**（`kanban/bin/kb` の純関数 `run_note(prev, text)` 1 か所）。
   `kb sync --dry-run` が出す `after.note`（console の下見が読む）も合成後の値にする。
3. **`note` の持ち主は人**。`kb set --note` / `kb block --note` / `kb done --note` は今までどおり `note` 全体を書き
   （`[run] ` 行も消える）、次の run が自分の 1 行目を書き直す。`--note ''` で空に戻せるのも変えない。
4. **長い申し送りはチケット本文の `## PM 補足`**（`kb append --section`）に置く。`note` は状態の要約という
   位置づけを保ち、`note` の行数を無制限に増やさない。
5. **既存チケットの移行はしない。** `[run] ` を付ける前に kb 自身が書いていた文言の書き出し
   （`LEGACY_RUN_PREFIXES`: 「人間へ」「PR 待ち」「再走中」「VM を取得できず」「VM の空き待ち」「自動マージ」
   「マージ済み」「PR 無しで終了」「runner が」「貸出直後の準備」「鍵の利用枠切れ」「鍵が使えず」
   「Claude の鍵が無いので」）で 1 行目が始まるなら run 由来と見なして捨てる。DB の移行スクリプトは要らない。

## 検討して採らなかった案

- **別フィールド（`run_note` / `last_run_status`）に分ける**（チケットの案 1）。表示は分かりやすいが、`kb` の SCHEMA 追加と
  既存 `kanban.db` の ALTER TABLE、`console/lib/core.py` の SELECT、`console/static/app.js` の表示、MCP の schema まで
  変更が広がる。まず「人の文が消えない」を最小の変更で満たし、欄を分けたくなったらこの ADR を上書きする。
- **人の行を run が消さないよう `kb set --note` も追記にする**。人が書き直す・消す口が無くなる。`note` の持ち主は人のままにする。

## 結果

- `kb run` / `resume` / `--from` / `kb sync` / `dispatch`（`kb run` を呼ぶだけ）のどの経路でも、PM の申し送りは残る。
- 一覧（BOARD.md / console / MCP）は `note` を文字列のまま出すので、`[run] ` の接頭辞で機械の行と人の行を見分けられる。
  改行は板と画面で空白に潰れて 1 行に見えるが、区別は接頭辞で付く（欄を分けるのは上の「採らなかった案」のとおり別の話）。
- 人の文が `LEGACY_RUN_PREFIXES` の言葉で始まっていると 1 回だけ捨てられる。`kb history` には残るので復元できる。
- `glue/bin/dispatch` の `kb block --note "dispatch: …"`（project.yml が無いチケット）は「機械が人の口（`kb block`）で
  書く」経路なので、この ADR の規則の外にある。同じ問題を持つが、別チケットで扱う。
