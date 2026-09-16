# ADR 0086: 人が動くまで解けない一時停止だけが残った板でも PM の `state` は `idle` のまま。人の出番は状態ではなく facts と画面で伝える

- 状態: Accepted
- 日付: 2026-09-16
- 関連: ADR-0074 決定 2（4 状態は導く）・決定 5（判断ログの語彙）、ADR-0079 決定 5（「別票で決める」の答え）、
  ADR-0077（`blocked_by_dependency`）、ADR-0043 / ADR-0046（一時停止と鍵待ち）

## 状況

`kb resumable` で一時停止中の票は、PM（`core.pm_pick_next()`）も配車（`dispatch`）も飛ばす。
候補が全部そうなら `next.reason` / `reason_code` は `blocked_by_pause` になる（ADR-0079 決定 5）。

この 1 語の中に、**解け方の違う 3 つ**が混ざっている（見分けるのは `skipped_by_pause` の中身）:

- 利用枠切れ（`paused: quota`・`until` に解除時刻）→ 時刻が来れば timer（`dispatch --resume-paused`）が続きを回す
- 鍵待ち（`paused: nokey`・`until` は `null`）→ **人が鍵を登録するまで解けない**
- 回数超過（`hits_exceeded`）→ **人が枠を確かめるまで自動では回さない**

後ろ 2 つは自動再開の待ち行列に入らない（`dispatch --resume-paused` は `ready` の票しか回さず、
`ready` は鍵プールの中身で決まって時間では変わらない）。**timer は一生拾わない。**

ADR-0079 決定 5 は「まず『時刻で解ける』と言い切らない」ことだけを範囲とし、
**後ろ 2 つだけが残った板を PM がどう知らせるか**（語彙を分けるか）は別票に残した。本 ADR がその答えである。

経緯として、この論点は一度実装まで進んでいる。#582（「鍵待ちで止まった票が誰にも知らされない」）の run で、
実装役と reviewer が**独立に**次を選んだ:

1. 新しい理由コード `blocked_by_key` を `PM_NEXT_REASONS` / `PM_REASON_CODES` に足す
2. 鍵待ち・回数超過しか残っていない板では `state` を `idle` → `blocked`（人の確認待ち）に格上げする
3. PM 画面に「どの票が・何を待っているか」を 1 票 1 行で出す

管理役は 1・2 を戻し、3 だけ残して着地させた。戻した理由は「bug 票の中で公開済みの契約（ja/en の
`guides/console.md`・`console/tests/` の assert・ADR-0074 決定 2）を覆さない」であって、
**案そのものは却下していなかった**。本 ADR はその判断を、bug 票の外で正面から決める。

### 実測（本 ADR を書いた時点。参照は関数名・定数名で書く。行番号は古びる）

- `core.PM_NEXT_REASONS` / `core.PM_REASON_CODES` に `blocked_by_key` は無い。
  ソースのコメントにも「人が動くまで解けない側（鍵待ち / 回数超過）も `blocked_by_pause` のまま言う。
  語は増やさない（ADR-0074 決定 2）」と宣言が降りている。
- `core.pm_status()` が `state = "blocked"` を立てる経路は **2 つだけ**:
  ①板が読めない（`board.readable` が偽）、②終わった run の `run_outcome().reason` が
  `PM_BLOCKED_REASONS` で、票が `done` でなく、`_pm_requeue_check()` が再投入を認めない。
  一時停止だけが残った板はどちらにも当たらず `idle` のままで、そう書いたコメントも同じ関数の中にある。
- 公開契約（ja/en の `guides/console.md` の `GET /api/pm` の行）は #581 で拡張され、
  「**この状態は `blocked` に格上げしませんが、『放っておけば機械が片付ける』という意味ではありません**」と、
  3 つの解け方の見分け方まで書いている。**「待てば解ける」という誤読は、状態を増やさずに文書で潰してある。**
- 画面は `app.js` の `renderPm` が `next.why` の `skipped_by_pause` を直接読み、`strings.js` の
  `T.pm.pause.nokey / hits / quota` で 1 票 1 行に出す（#582 で着地）。**語彙には依存していない。**
- `console/tests/test_next_preview.py` の `assertNamesWhatItWaitsFor` が、鍵待ち・回数超過それぞれについて
  `state == "idle"` / `reason == "blocked_by_pause"` / `launchable` が偽 / facts に待ち先が出ることを検査している。

## 決定

**案 A（現状維持）を採る。** 差分はこの ADR だけで、コード・文言・テスト・`changelog.d/` は変えない。

1. 鍵待ち（`paused: nokey`）と回数超過（`hits_exceeded`）しか残っていない板でも、`pm_status()` の
   `state` は **`idle` のまま**にする。`blocked` へ格上げしない。
2. 理由の語彙を増やさない。`next.reason` / `reason_code` は **`blocked_by_pause` のまま**で、
   `blocked_by_key` のような語を `PM_NEXT_REASONS` / `PM_REASON_CODES` に足さない。
3. 人の出番は**状態の語ではなく事実で伝える**。`proposal.facts.skipped_by_pause`
   （`paused` / `until` / `needed_keys` / `hits_exceeded`）が「どの票が・何を待っているか・誰が動けば解けるか」を持ち、
   画面がそれを 1 票 1 行で出す。文書（`guides/console.md`）が「時刻では解けない 2 つがある」と案内する。

**ADR-0074 決定 2 は読み替えない。**代わりに、決定 2 の `blocked` の意味の境界をここで明文化する:

> `blocked` は「**PM が自力で何も起こせない**」——板が読めない、または止まった run が人の判断を待っている——に限る。
> 「**板は読めていて、確かめた結論として今は選べない**」は `idle` である
> （`blocked_by_dependency`（ADR-0077）も `blocked_by_pause` も同じ側）。

なお決定 2 の本文は `blocked` の材料に「票の `status == "blocked"`」も挙げているが、実装の `pm_status()` は
**票の状態から `state` を立てていない**（`blocked` の票は `board.blocked` / `board.blocked_n` と facts に出る）。
本 ADR は上の境界を正とし、この差を直す作業は範囲外とする（既存 ADR は書き換えない）。

## 理由

- **案 B の動機は、状態を増やさずに解消済み。** 案 B の根拠は「『一時停止』という語が『待てば解ける』と読ませる」
  だった。その誤読は #581 が ja/en の公開文書で名指しで潰し、**3 つの解け方の区別**まで書いた。
  `blocked_by_key` 1 語で表せるより細かい情報が、既に読める形で出ている。
- **人の出番は既に読める。** #582 で着地した画面の 1 票 1 行と `skipped_by_pause` の facts があるので、
  人は「どの票が・何を待っているか」を状態の語に頼らず読める。状態を格上げしても、
  **どの鍵が要るのかは結局 facts を読まないと分からない**。増やした語が仕事を減らさない。
- **`blocked` の意味が濁る。** `blocked` は今「PM が自力で何も起こせない」であり、人がやることは
  「止まった run を見る」か「板を直す」。鍵待ちを混ぜると、`run_outcome` 由来の `blocked` と
  区別するための旗（#582 の実装では `key_block`）が**状態機械の外に**増える。
  4 状態で足りているという ADR-0074 決定 2 の前提が、旗の数だけ実質崩れる。
- **過去の判断ログが古びない**（ADR-0074 決定 5）。`blocked_by_pause` で積まれた行の意味を変えないので、
  後から数え直したときに「どの版で書かれた行か」を気にせずに済む。
- **意図が二重に記録されている。** 「語を増やさない」は ADR-0074 決定 2 だけでなく `core.py` のコメントにも
  降りていて、公開文書とテストが同じことを言っている。覆すなら 4 か所を同時に動かす価値が要るが、
  上のとおり利用者が得るものが無い。

### 採らなかった案

**案 B: `state` を `blocked` へ格上げし、`blocked_by_key` を足す。**
「板に鍵待ちしか無いのに `idle`（回すチケットを待っています）と出る」のが、画面を見ない運用
（MCP / 判断ログだけを読む）では人の出番が伝わらない、という指摘は正しい。実装役と reviewer が独立に
選んだ重みも認める。採らなかったのは、(1) 動機が #581 の文書修正で既に解消していること、
(2) `run_outcome` 由来の `blocked` と区別する旗が状態機械の外に増えること、
(3) 公開契約（ja/en）とテストを同時に覆す割に、鍵の特定には結局 facts が要ることによる。

**案 C: 状態は増やさず `next.reason` に `blocked_by_key` だけ足す。**
ADR-0074 決定 2（状態を増やさない）は守れる。採らなかったのは、(1) **1 語では 2 種類の人待ち
（鍵の登録待ち・枠の確認待ち）を区別できず**、facts を読む必要が消えないこと、
(2) 過去の `blocked_by_pause` 行の意味が「以後は quota だけ」に変わり、決定 5 の「過去の行が古びない」に
抵触すること。ADR-0077（`blocked_by_dependency`）や #581（`blocked_by_pause`）が語を足した先例は
**まだ facts で区別できていない新しい事実**を足したのに対し、本件は既に `skipped_by_pause` で区別できている。

## 結果（トレードオフ）

- **画面を見ない運用では `state` だけで人の出番を知ることはできない。** MCP の `pm_status` や判断ログを読む側は
  `proposal.facts.skipped_by_pause` を見る必要がある。これは受け入れる負担で、`guides/console.md` が
  「`blocked_by_pause` が続くときは `paused` / `until` / `needed_keys` / `hits_exceeded` を見てください」と
  既に案内している。
- **放置は検知されない。** 鍵待ちのまま何日も動かない板を PM が自分から騒ぐことはない。
  将来それが要るなら、**状態を増やすのではなく、facts を読む側（通知・集計）で作る**。
  「`blocked_by_pause` が N tick 続いた」は判断ログから数えられる（決定 5 の語彙で書いてあるため）。
- 本 ADR の決定は差分ゼロなので、利用者に見える変更は無い（`changelog.d/` も足さない）。

## 未確認

- 実データの判断ログ（`$AIFACTORY_WORKSPACE/logs/pm-decisions.jsonl`）の行は確認していない。
  本 ADR を書いた環境では運用データが無く、読めるのはコードとテストが作る一時 workspace だけ。
  過去行の意味を変えない決定なので、確認できなくても決定は成り立つ。

## 状態

Accepted。ADR-0074 決定 2 の `blocked` / `idle` の境界を明文化し、ADR-0079 決定 5 が別票へ送った
「後ろ 2 つだけが残った板を PM がどう知らせるか」に答える。既存 ADR は書き換えない。
