# ADR 0043: 鍵の利用枠切れで止まった run は「wip を保全して一時停止し、解除後に機械が続きから回す」

- 状態: Accepted
- 日付: 2026-09-09
- チケット: 380

## 状況

agent step は VM の中で `claude -p` として動き、Claude の鍵（`CLAUDE_CODE_OAUTH_TOKEN`）の**利用枠**に縛られる。
5 時間の窓・7 日の窓・overage の枠のどれかを使い切ると、CLI は `rate_limit_event`（`status: rejected`、`resetsAt` に解除時刻）を
流したあと result を `is_error` で閉じて rc≠0 で終わる。鍵そのものが無効・失効・残高不足でも同じく rc≠0 で終わる。

runner はこれを**普通の失敗**として扱っていた。結果は 3 つとも悪い。

1. `on_fail` の戻し（gates → implement は 2 回、review → implement は 1 回）を 1 回消費してから human 行きになる。枠が戻らないうちの
   戻しは同じ所で止まるだけで、本来の「直す機会」を無駄に使う
2. 未コミットの変更が wip ブランチに乗らない。保全（`commit_tracked`）は時間上限（rc=124。ADR-0036 の前提になった #329）だけが
   していて、rc=1 の経路は素通りする。40 分書いた実装が release の巻き戻しで消える
3. 再開は人が板を見て `kb run --from` を打つしかない。解除は深夜や週末に来るので、そのぶん工場が止まる

鍵の利用枠切れは「その step が悪い」のではなく「今は回せない」という状態で、VM の空き待ち（`--wait` / `wait_timeout`。ADR-0031）と
同じ性質を持つ。待てば戻る失敗を人間待ちにしない、という線は既に引いてある（`kb` が `wait_timeout` を todo に戻す）。

## 決定

1. **runner が見分ける。** agent が rc≠0（または result が `is_error`）で終わったとき、`rate_limit_event` の `rejected` と result /
   stderr の文言から `quota`（利用枠。待てば戻る）と `key`（鍵が使えない。人が直す）を判定する。tool_result の中身は見ない
   （agent が読んだコードに "Invalid API key" と書いてあるだけで鍵の失効と誤読しない）。判定は `classify_agent_stop()` 1 か所。
2. **どちらも追跡済みの変更を wip コミットして保全し、戻しの回数を消費せず human で終わる。** コミットの題は
   `wip: usage limit` / `wip: token unusable`。`resume_step` は戻し先ではなく**その step 自身**（wip の続きを同じ役割が引き取る）。
3. **state.json に足すのは事実だけ。** run 全体に `failure: "quota" | "key"`、`quota_type`（five_hour / seven_day / … / unknown）、
   `retry_after`（`resetsAt` をオフセット付き ISO に。分からなければ null）、`quota_hits`（続けて利用枠で止まった回数。`--from` の続きは
   前回の値を引き継ぐ）。history の末尾にも `failure` / `quota_type` / `retry_after`。再開コマンドの文言は書かない（ADR-0025 / ADR-0036）。
4. **kb は `quota` を todo に戻し、`key` は blocked にする。** どちらもメモに `kb run <id> --from <step> --branch <wip>` を置く。
   `quota_hits` が `AIFACTORY_RESUME_MAX_HITS`（既定 6）に達したら todo に戻さず blocked（鍵の枠が小さすぎる等、機械では直らない）。
5. **続きを回すのは配車（dispatch）で、常駐は systemd timer。** `kb resumable` が「一時停止中のチケットと、解除時刻（`retry_after`。
   無ければ `finished` + `AIFACTORY_RESUME_BACKOFF_MIN` 分、既定 30）を過ぎたか」を返す。`dispatch --resume-paused` はそのうち過ぎたものだけを
   古い順に `kb run <id> --from` で回す。制御系の `aifactory-resume.timer` が 5 分ごとに呼ぶ（`install.sh --systemd` で登録）。
   通常の `dispatch` も一時停止中のチケットは解除前なら飛ばし、解除後なら `--from` で続きから回す（初めからやり直さない）。
6. **VM は持ち続けない。** 解除まで最長で 5 時間〜数日あり、プールは PJ あたり 2〜3 台。wip に保全して release し、続きは新しい VM で
   （`--from` の経路。ADR-0036）。

## 理由

- **なぜ runner の中で待たないのか。** 待つ間 VM を占有する上、runner のプロセス（ssh 越しのジョブ）が数時間生きている前提になる。
  制御系の再起動や ssh の切断で待ちごと消える。「記録に残して終わり、別のプロセスが拾う」形なら、何が死んでも記録から続けられる。
- **なぜ dispatch に載せるのか。** 「todo を取って `kb run` を呼ぶ・プールの空きを見る・ログを残す」は配車が既に持っている。
  別コマンドに複製すると判断が 2 か所になる。`--resume-paused` は対象を「一時停止中」に絞るだけ。
- **なぜ todo なのか（新しい状態を作らないのか）。** `wait_timeout` と同じ扱い。状態を増やすと console / MCP / kb / BOARD の全部に
  波及する。todo + run の記録（`failure: quota`）で「一時停止中」は導ける（`kb resumable` がその導出の正本）。
- **なぜ `key` は自動で回さないのか。** 鍵の失効・残高不足は時間で戻らない。回しても VM を取り直して同じ所で止まるだけ。
- **なぜ回数に上限を置くのか。** 解除時刻が読めない回（旧形式の文言・stderr だけ）は 30 分ごとに VM を取り直すことになる。
  枠が極端に小さい鍵では永久に繰り返すので、6 回で人へ返す。上限は環境変数で変えられる。

## 結果（トレードオフ）

- 続きの run は wip の上で**同じ step を最初から**回す。implementer は依頼文で「wip の続きから、やり直さない」と言われるが、
  途中の思考は失われている（timeout と同じ）。長い step は 30 分ごとの wip コミット（#329）が効く。
- `kb next`（console の「配車する」の下見）は解除前の一時停止チケットも「次」と見せる。実際の配車は飛ばす。下見の表示は別票。
- 同じチケットを 2 つ同時に再開してはいけない制約（ADR-0036）はそのまま。timer と人の `kb run --from` が重なると、後から
  `preserve()` した方が wip を上書きする。timer は todo のものしか拾わないので、人が回し始めれば（in_progress）重ならない。
- 文言による判定（`QUOTA_TEXT` / `KEY_TEXT`）は claude CLI の出力に追随が要る。`rate_limit_event` があればそちらが優先なので、
  文言が変わっても解除時刻付きの本命は壊れない。
