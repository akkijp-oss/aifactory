# ADR 0027: 起票・配車のログの表はコンソール側で導く

- 状態: Accepted
- 日付: 2026-09-08

## 決定

ログ画面（`#/logs`）に出す「日時・処理・PJ・チケット・結果・理由」の表は、コンソール（`console/lib/core.py` の `parse_intake_line` / `parse_dispatch_line` / `logs_view`）が既存のログ行から導く。`glue/bin/intake` と `glue/bin/dispatch` が書く行の形式は変えない。

これにより **両ログの行形式はコンソールが読む契約になる**。

- `intake.log`: `日時 / id / pj / kind / confidence / model / reason` の 7 列（tab 区切り）
- `dispatch.log`: `日時 tab 本文` で、本文は `end` / `start` / `project.yml 無し → blocked` / `Pull worker unavailable → skip` / `プール n 台すべて貸出中` / `todo が無い` の 6 種類

`logs_view()` は生のテキスト（`intake` / `dispatch`）を今までどおり返したまま、導いた行の一覧（`entries`、新しい順、上限 1000 件）と分解前の件数（`total`）を足す。生のテキストは画面の「元のログを見る」と MCP の `logs` ツールが読むので消さない。

どの規則にも当てはまらない行は捨てず、`event: "other"` として原文（`raw` / `reason`）をそのまま出す。理由は記号（`worker_unavailable` / `pool_busy`）までコンソールが決め、日本語は `console/static/strings.js` の `T` で引く。

## 理由

ログは運用確認のたびに読むのに、生テキストのままでは `rc=2` / `status=todo` / 見出しの無い `0.9` の意味を読む側が知っている必要があり、`console/UX.md` の「開発者の語彙を表に出さない」に反していた。見つけたチケットへ移る導線も無かった。

分解を glue 側に寄せて JSON 行などに変えると、過去のログが読めなくなり、ログを見る人（`tail -f` している運転員）と配車の実装が同時に変わる。表示側で導けば、過去の記録にも同じ表が出て、glue の変更は要らない。ADR-0025（実行記録の停止理由をコンソール側で導く）と同じ構図。

## 制約

- ログ行を変えると表が「その他」に落ちる。`glue/bin/intake` / `glue/bin/dispatch` の行を変えるときは `parse_*_line` と `console/tests/test_console.py` の検査を一緒に直す
- `dispatch.log` は行ごとに列の有無が違う。チケット番号・PJ の無い行（`todo が無い`）があるので、表の一部の欄は空になる
- 生ログの読み出しは末尾 200,000 文字の tail なので、切れ目の先頭 1 行は不完全になる。`truncated` のときはその 1 行を捨てる
- 起票の理由（自由文）と配車の題名より細かいことは出せない。工程ごとの記録は実行記録、状態の履歴はチケットで見る
