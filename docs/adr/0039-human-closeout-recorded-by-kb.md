# ADR 0039: 人間の後始末は kb が run 記録に転記する（`result` は上書きせず `human` を別に持つ）

- 状態: Accepted（ADR-0025 を補う。既存の決定は変えない）
- 日付: 2026-09-09

## 状況

`chore` / `bug` / `feature` の run は、レビューが通らなければ `result: human` で終わる。wip ブランチは push されているので、PM がそこから PR を作ってマージすれば仕事は片付く。片付いた後の記録がこうなる。

- チケットは `kb set <id> --pr N` / `kb done <id>` で done にできる。
- **run の `state.json` は `result: human`・`pr_url: ""` のまま**。書き手は runner（`workflow/bin/run`）だけで、runner は実行を終えた時点で消える。後から書く口が無い。
- コンソールの `#/run/<name>` は `run_outcome()` が `result == "human"` を無条件に `waiting` に落とすので、「工程は終わり、人間の判断を待っています」を出し続ける。続きから回すコマンド（ADR-0036）も出たままになる。

その結果、後から run を見た人が「この run はどう処理されたのか」を追えない（kumitate #292 で実際に質問が出た）。同じ場所で、再走（同名 run の `-attemptN` 退避）を始めてもチケットのメモが前回の「人間へ（wip: …）…」のままで、板が古い状態に見えていた（#294）。

## 決定

1. **`state.json` の書き手を runner と kb の 2 つにする。** ただし kb が書いてよいのは `human` キーだけで、runner が書いた項目は読むだけにする。
2. **`human: {at, by, result, pr_url, text}` を追記する。** `result` は `done`（人間が仕上げた）か `abandoned`（打ち切った）。runner が確定した最上位の `result`（`human` / `end` / `failed`）は**上書きしない**。
3. **書く口は kb に置く。** `kb run-note <run> [--result done|abandoned] [--pr N] [--text T] [--force]`。console と MCP（`run_action`）は kb を呼ぶだけで、`state.json` を直接書かない（ADR-0013 の「状態を変えるのは CLI 経由」を守る）。
4. **チケットに PR 番号を入れた・done にしたときは自動で転記する。** `kb set --pr N` / `kb done` から、紐づく run が `result: human` でまだ `human` が無いときだけ。runner 由来の更新（`apply_result`）からは転記しない。
5. **表示は今までどおりコンソールが導く。** `run_outcome()` が `human` から `human_done` / `human_abandoned` を出し、`resume`（続きから回すコマンド）を消す。`state.json` には文言を書かない。
6. **書き込みは終わった run にだけ。** `finished` の無い（＝ runner が動いている）run への `run-note` は断る。既に `human` があるときも `--force` なしでは断る。

## 理由

- **なぜ runner ではなく kb か。** 後始末は run が終わった後の出来事で、runner はもう居ない。人間の操作の入口は既に kb（`kb done` / `kb set --pr`）に集まっており、console も MCP もそこを通る。書き手を 1 つ増やすだけで済む。
- **なぜ `result` を上書きしないか。** `result` は「runner がどう終えたか」の事実で、後から変えると `apply_result()` の状態導出・`run_liveness` の判定・過去の記録の読み方が全部ずれる。事実を足して、読み方は導く側で決める（ADR-0025 と同じ分業）。
- **なぜ `apply_result` から転記しないか。** runner が `pr` step で作った PR も `kb sync` 経由で `update(pr=…)` を通る。区別しないと runner の PR が「人間が仕上げた」として記録される（誤記録）。
- **なぜ書き込みを終わった run に限るか。** runner は実行中 `state.json` を書き直す。同じファイルを 2 人が書くと、どちらかの書き込みが消える。`finished` の有無は「runner がもう触らない」の目印として既にある。
- **なぜ `pr_url` を URL にするか。** PJ 定義（`project.yml`）に `repo` があれば `https://github.com/<repo>/pull/<n>` を組む。無ければ `#N` を入れる。コンソールは `/pull/(\d+)` と `#(\d+)` の両方から番号を取るので、どちらでも「PR #n で仕上げました」と読める。

## 結果（トレードオフ）

- `state.json` を書くのは runner だけ、という不変条件は無くなる。`human` 以外を kb が書きたくなったら、その都度 ADR を足す。
- kb が `workflow/bin/run` の退避規則（`-attemptN`）と同じ数え方でチケットのメモに attempt 番号を書く。runner が保証する値ではないので、ずれても害はメモの文言だけに留める（番号を厳密に合わせるには runner の変更が要る）。
- 人が 2 人続けて後始末を記録すると、後の方は `--force` を求められる。事故で説明が消えるより、断る方を選ぶ。
- 再走を始めるとチケットのメモが「再走中（attempt N・workflow W）」に置き換わる。前の文言は `kb history` に残る。
