# ADR 0049: pull worker を他の run が使っているのは「待てば解ける失敗」（`PoolBusy`）として扱う

- 状態: Accepted
- 日付: 2026-09-10
- チケット: 373

## 状況

pull backend（macOS / Windows / Linux）の worker は `leases` テーブルに worker あたり 1 行しか持たず、PJ を跨いで
共有される 1 台である（`project.yml` の `worker` に同じ id を書けば複数 PJ が同じ Mac を使う）。`Store.acquire` は
lease id が違えば PJ に関係なく `worker leased to another run`（409）を返す。

2026-09-09 12:09 UTC の実測: asura #313 を `kb run 313 --wait 30` で起動したところ、mac-worker-01 は termarium #251 に
貸出中で、`MacRun.take` が lease を見ずに `acquire` を呼び、409 をそのまま例外にして 0 秒で終了した。ADR-0031 の
`take_waiting` は `PoolBusy` しか再試行しないので `--wait 30` は効かず、`failure` の目印が無い `failed` は 238 の規則で
チケットが `blocked` になった。さらに `fail_before_start` は lease を取れていないのに
「Mac VM: 調べられるよう lease は残す」と記録し、「自分の lease が残っている」と読める誘導をしていた。

ADR-0031 の結果欄には「pull backend は `PoolBusy` を上げない。`--wait` はサンドボックス backend にだけ効く」と
書いてあり、この振る舞いは当時の設計どおりである。一方で「他 run が使用中」は Proxmox のプール満杯と同じく
**待てば解ける**失敗で、人が直せるものは何も無い。dispatch も lease / 操作が残っている worker は失敗させず見送る。

## 決定

1. **pull backend の「他 run が lease を持っている」「前の操作（queued / running / uncertain）が残っている」は
   `PoolBusy` にする。** `--wait N` は全 backend で「空くまで最大 N 分待つ」の意味になり、待ちの表示（`wait-vm`）と
   上限超過の目印（`failure: "wait_timeout"` / `waited_s`）は ADR-0031 のまま変えない。
2. **判定は runner 側で lease を見て行う**（`workers()` の `lease` / `operation`）。`Store.acquire` の 409 文言に頼るのは
   見てから取るまでのレースを包み直すときだけにして、queue の文言への依存を最小にする。
3. **`PoolBusy` に「誰がいつから使っているか」の 1 行（`reason`）を持たせる。** runner はそれを `state.json` の
   `wait_reason` に残し、`kb` は note にそのまま写す（`mac-worker-01 は 2026-09-09-termarium-251 が使用中（…）`）。
   保持者の run 名は `runs/<run>/state.json` の `lease` から引く（見つからなければ lease id）。
4. **`reason` を持つ `PoolBusy` は、`--wait` を付けていない run でも `failure: "wait_timeout"` で終える**（`waited_s` は
   書かない）。チケットは `blocked` ではなく `todo` に戻り、空き次第 dispatch が拾う。dispatch は lease 有りの worker を
   見送るので、即失敗の繰り返しにはならない。Proxmox のプール満杯（`reason` 無し）は従来どおり即 `blocked`。
5. **`--wait` で待てるのは「取り合い」だけ。** worker が offline / lifecycle 無しは従来どおり即失敗にする（設定か常駐の
   問題で、待っても直らない）。基準 VM・ネットワークの準備待ち（最大 6 時間）も従来どおり take の中で待つ。
6. **「調べられるよう lease は残す」は、この run 自身の lease が `state.json` にあるときだけ記録する。** 無いときは
   「lease は取っていない」と書く。

## 検討して採らなかった案

- **`Store.acquire` の 409 文言だけで分類する**（runner は lease を見ない）。実装は 1 行で済むが、queue 側の文言を
  変えると静かに「待たなくなる」。lease を先に見れば、文言が変わっても取り合いは検出できる。
- **offline の worker も `--wait` で待つ**（チケットの案）。登録ミスや常駐停止と区別できず、上限まで待ってから人へ返す
  ぶん発見が遅れる。offline は今までどおり即座に人へ返す。
- **`Store.acquire` を待つ（queue 側でブロックする）**。sqlite のトランザクションを長く握ることになり、他の worker の
  操作まで止まる。待つ場所は runner の 1 か所（ADR-0031 の決定 1）に保つ。

## 結果

- ADR-0031 の結果欄の「pull backend は `PoolBusy` を上げない。`--wait` はサンドボックス backend にだけ効く」は
  **この ADR で置き換える**（ADR-0031 の本文は書き換えない）。
- 共有の Mac を複数 PJ で使う運用が、人の再投入なしに回る。待っている run はチケットを `in_progress` のまま持つので、
  二重起動は起きない（ADR-0031 の結果欄と同じトレードオフ）。
- 待ちの理由は `state.json` の `wait_reason`・チケットの note・runner のログの 3 か所に同じ 1 行で出る。
- 保持者の run 名は `runs/*/state.json` の走査で引くので、run が増えると少しずつ遅くなる（glob 1 回・JSON を読むだけ)。
  壊れた `state.json` は無視して lease id にフォールバックする。
- windows / linux backend も同じヘルパ（`workflow/lib/macos.py` の `acquire_lease`）を通るので、扱いは 3 つで揃う。
