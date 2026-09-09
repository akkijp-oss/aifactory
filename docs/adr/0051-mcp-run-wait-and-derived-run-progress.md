# ADR 0051: run の工程遷移は `run_wait` で待つ／経過秒とゲート一覧は表示側で導く

- 状態: Accepted（ADR-0025 / ADR-0028 / ADR-0036 / ADR-0038 を補う。既存の決定は変えない）
- 日付: 2026-09-10

## 状況

2026-09-09 に PM が MCP だけで 13 件の run を 1 件ずつ回した。1 run は plan → implement → gates → review → sync → pr で 13〜40 分かかる。手段は `job_wait`（上限 300 秒・末尾 4000 文字のログ）しか無く、実際に起きたのはこうだった。

- 1 run あたり `job_wait` を 4〜8 回呼び、返ってきたログの末尾を毎回パースして「今どの工程か」「gates が赤か」を組み立てた。
- agent 自身の出力に `result: success` や `rc=0` が混ざるので、ログ本文からの終了判定は誤る。
- 結局 MCP を離れ、制御系に ssh して `cat <job>/log` を 60 秒おきに走らせ `^\[run ` の行だけを拾う監視スクリプトを自作した。
- ゲートの合否は `code-gates-N.log` を `read_file` で開かないと分からなかった。

材料は既に全部 `state.json`（`history[]{step, ok, next, at}` / `current{step, kind, log, since}` / `next` / `result` / `pr_url`）と `work/gates.txt`（`PASS` / `FAIL` / `INFO` の 1 行 1 ゲート）に載っている。足りないのは「記録を読み直して、変わった瞬間に構造化して返す口」だけだった。

## 決定

1. **`run_wait(name, until, timeout_s)` を足す。** `state.json` を 1 秒間隔で読み直し、基準（`history` の件数 / `current.step` / `current.since` / `result` / `finished`）が変われば即返す。`until: step`（既定）は工程遷移まで、`until: result` は run が終わるまで。既に終わっている run は待たずに返す。
2. **`run_wait` はログ本文を返さない。** 返すのは `{name, until, changed, waited_s, status, step, ok, next, result, pr_url, gate_fails, gates, reason, current, history, elapsed_s, finished}` だけ。ログを読むのは `read_file` の仕事で、run_wait は「どこまで進んだか」だけを答える。
3. **上限は `job_wait` と同じ 300 秒に据える（`RUN_WAIT_MAX_S`）。** チケット #340 の本文は 1800 秒を求めていたが、採らない（理由は下）。上限は定数 1 つにまとめ、後から上げるのが 1 行で済む形にしておく。
4. **`run_wait` を 2 つ目の `ASYNC_TOOLS` として認める。** 足してよいのは「待つ以外に何もしない・工場の状態を変えない」ツールだけ。`run_wait` は `state.json` と `work/gates.txt` を読むだけなのでこれを満たす。`annotations` は `readOnlyHint: true`（ADR-0038）。
5. **経過秒とゲート一覧は表示側（`console/lib/core.py`）が既存の記録から導く。** `run_detail()` に `progress` を 1 つ足す（`{elapsed_s, current{step, kind, since, elapsed_s}, history[]{…, elapsed_s}, gates[]{name, status, note}}`）。`workflow/bin/run` と `state.json` のスキーマは変えない（ADR-0025 / ADR-0036）。
6. **工程の経過秒は「前の工程が終わった時刻から自分が終わった時刻まで」。** 1 件目は `state.started` から測る。時刻が欠けている・逆順の記録では `null` にして、推測で補正しない。
7. **`work/gates.txt` の読み取りは `gate_results()` に一本化する。** `PASS` / `FAIL` / `INFO` を書いてあるとおりに返し、既存の `gate_fails()` はその上の薄い絞り込みにする。`=== <ゲート>.log` の行から先を見ないという既存の考え方（ログ本文中の `FAIL` を拾わない）はそのまま残す。

## 理由

- **なぜログ本文を返さないか。** ログ本文からの終了判定が誤るのが、この件の実害そのものだった。返り値に本文を入れなければ、呼び手はパースする気にならない。判定の根拠は runner が書いた `history` / `result` の 1 か所に寄る。
- **なぜ上限を 1800 秒にしないか。** ADR-0028 が 300 秒に据えた理由は「Claude Code は 120 秒でツール呼び出しをバックグラウンド化するので、それ以上待たせても呼び手に届く形にならない」で、これは今も変わっていない。1800 秒待たせても、返るのは呼び手が既に別の話をしている後になる。この件で効いたのは待てる長さではなく戻り値の形（構造化・ログ本文なし）で、`changed: false` で返る繰り返し呼び出しの方が運転しやすい。チケット本文より新しい PM 補足（2026-09-10）も 1800 への言及を落としている。**この 1 件はチケットの完了条件を満たしていない。上げるなら ADR-0028 の理由の方を先に覆すこと。**
- **なぜ `state.json` を変えないか。** 経過秒もゲートの一覧も、既にある記録から引き算と行の読み取りだけで出る。記録に導出値を書くと、runner と表示側の 2 か所で同じ答えを持つことになり、食い違ったときにどちらが正か決められない（ADR-0025 / ADR-0036 と同じ理由）。
- **なぜ `INFO` も一覧に出すか。** base でも赤かったゲートは runner が `FAIL` → `INFO` に格下げしてある（ADR-0038）。表示側で格下げを判断し直すと runner と食い違うので、書いてあるとおりに `status` として返し、読む側が区別できるようにする。
- **なぜ `run_wait` の `gate_fails` を `run_outcome` だけに任せないか。** `run_outcome` がゲートを読むのは「失敗した工程で止まった run」のときだけで、走っている最中は空になる。走っている最中こそ知りたいので、空なら同じ `work/gates.txt` から引く。判定を二重に書かないよう、読むのはどちらも `gate_results()` 1 つ。

## 結果（トレードオフ）

- MCP だけで「次の工程遷移まで待つ → 結果を見る」が `run_wait` 1 呼び出しでできる。ssh も監視スクリプトも要らない。
- 1 工程が 40 分かかる run では `run_wait` を 8 回以上呼ぶことになる。上限を上げれば減るが、上の理由で今は据え置く。
- `--from` で再開した run や VM の空き待ちを挟んだ run では、工程の外で過ぎた時間が経過秒に混ざる。runner はその境目を記録していないので補正できない。この性質はドキュメントと `run_show` の説明文に書く。
- `workflow/bin/run` の `save()` は tmp+rename ではない直書きなので、1 秒間隔のポーリングが書き込み途中の壊れた `state.json` を掴みうる。読み側は例外を握って次の周回に回す（`run_summary` と同じ防御）。runner 側をアトミックにするのは別の話として残す。
- `run_detail()` の `progress` は Web コンソールの run 画面にも流れる。今回は画面のテンプレートを変えないので、画面の見た目は変わらない。
