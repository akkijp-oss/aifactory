# ADR 0031: VM の空き待ちは runner が 1 か所で行う／上限超過は `failure: wait_timeout` として未着手に戻す

- 状態: Accepted
- 日付: 2026-09-08

## 状況

`sandbox take` はプールに空きが無いとその場で `die` する（内訳つき。241）。呼ぶ側もリトライしないので、`workflow/bin/run` は開始前に `result: failed` で終わり、`kb run` はチケットを `blocked` にする（238）。

プールが 2 台のとき 13 件を回すには、人（または LLM）が各 run の終了を監視して、空いた瞬間に次を手で起動するしかなかった。2026-09-08 のチケット 234 の運用では、これを 7 波・約 3 時間ぶんやった。`dispatch` も同じ理由で、満杯の PJ はチケットを丸ごと飛ばす。

一方、234 で入れたロック（`state.json.lock`）は「空き VM の選定〜仮予約」だけを囲んでいる。したがって take を呼び直すこと自体は安全で、足りないのは「空くまで待つ」という 1 つの振る舞いだけだった。

## 決定

1. **待つのは `workflow/bin/run`（runner）だけ。** `sandbox take` 側（bash）には `--wait` を作らない。`kb run --wait <分>` と `dispatch --wait <分>` は runner に `--wait=<秒>` を渡すだけで、待機ロジックは 1 か所に置く。
2. 「空きなし」は `stderr` に runner の定数 `POOL_BUSY`（`に空きなし`）を含む take の失敗として判定し、`PoolBusy` に分類する。**それ以外の take の失敗は今までどおり即 `failed`**（待っても直らないため）。
3. 待っている間は `state.json` の `current` を `{"step": "wait-vm", "kind": "wait", "log": null, "since": <待ち始め>}` にする。`since` は待ち始めに 1 度だけ書く（毎回書き直すと画面の経過時間が振り出しに戻る）。console のボード・run 画面・MCP `run_show` は `current` をそのまま読むので、これだけで「VM の空き待ち（n 分）」が伝わる。表示名は `console/static/strings.js` の `T.step`。
4. 上限を超えたら `result: failed / next: human` は据え置きのまま、`failure: "wait_timeout"` と `waited_s` を足す。`kb apply_result` はこの目印のときだけチケットを **`todo`** に戻し、理由をメモに残す。目印が無い `failed` は 238 のとおり `blocked` のまま。
5. `dispatch --wait` は「満杯の PJ を飛ばす」事前チェックを飛ばし、`kb run --wait` に任せる。`--wait` 無しの `dispatch` の挙動は変えない。

## 理由

- 待機を bash と Python の 2 か所に持つと、内訳のログ・`current` の更新・pull backend（macos / windows / linux の `take()` 上書き）との関係を 2 回考えることになる。runner 側なら再試行のたびに記録を更新でき、pull backend には触らずに済む。
- `failed` 一本のままでは「VM が壊れている（人が直す）」と「空きが無かった（待てば回る）」を区別できない。238 が `todo` を嫌ったのは「配車がすぐ拾って同じ失敗を繰り返す」からで、`--wait` 付きの再実行は失敗を繰り返さず待つだけなので、この理由が当たらない。`result` の値は増やさず、目印を 1 つ足すだけにしたのは console の既存の導出（ADR-0025）を壊さないため。
- 判定を stderr の文言に頼るのは弱いが、`sandbox` の終了コードを分けるのは別の変更（bash 層の互換）になる。文言を変えたときに気づけるよう、目印は runner の定数を正本にし、テストもそれを参照する。文言が変われば「待たずに `blocked`」＝今までどおりの安全側に倒れる。

## 結果（トレードオフ）

- `--wait` 中の run は VM を借りていないのに「実行中」に見える（チケットも `in_progress`）。二重起動を防ぐにはこれが要る。待っていることは `current.step` で分かる。
- runner を途中で殺すと `current: wait-vm` のまま runner が居なくなるが、236 の中断（abandoned）判定がそのまま拾う。
- 上限超過で `todo` に戻したチケットは、次の `dispatch` が拾う。`--wait` 付きなら再び待つだけで、即失敗の繰り返しにはならない。
- pull backend（macos / windows / linux）は `take()` を上書きしていて `PoolBusy` を上げない。`--wait` はサンドボックス backend にだけ効く。
- 「同じ PJ の run を直列にする」は別の話なので、このチケットでは扱わない。
