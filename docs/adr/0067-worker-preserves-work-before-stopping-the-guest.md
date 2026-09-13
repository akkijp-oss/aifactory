# ADR 0067: worker はゲストを止める前に作業ブランチを wip へ push する（保全コマンドは制御系が payload で渡す）

- 状態: Accepted
- 日付: 2026-09-13

## 状況

ADR-0066 は事故の伝播を 4 段で書き、Python 側の 1〜2 と 4（制御系 sqlite のやり直し・runner の poll が
op を取り消さない・human 落ちで wip を push する）を直した。決定 7 で「worker 側は別票」と残した 3 段目、
**取り消しが worker に届いてしまった回**がここで扱う残りである。

worker は guest-exec を途中で止められないので、`workers/cmd/aifactory-worker/main.go` の
`cmdCtx.Err() != nil` 分岐で **ゲストごと停止**する。ゲスト内のコミット済み・未 push の実装はそこで失われる。

- 制御系側の保全（`workflow/bin/run` の `preserve()` / `MacRun.preserve`）はゲストが動いている前提で、
  ゲストが既に止まっていれば push できない。ADR-0066 の実測どおり、この経路の `preserve()` は
  worker が別の operation を抱えている（`worker is busy`）で弾かれる場合が多く、best effort にとどまる。
- つまり **止める側が最後の機会**を持っている。止める直前に 1 回だけ push できれば実装は残る。
- 実測（2026-09-13、`lifecycle_test.go` の取り消しテストに呼び出し記録を入れて確認）では、取り消し由来の停止は
  `main.go` の guest-exec 側の `stopGuest()` だけを通る。`lifecycle.go` の `stopGuest()`（guest-prepare の
  待ち受けと guest-release）は取り消し由来ではなく、別経路である。

worker 単独では push できない。ゲスト内の `$SANDBOX_APP_DIR`・`runtime.env`（`GH_TOKEN`）・作業ブランチ名・
wip 名（`sandbox/<task>-<wf>-wip`）はすべて **制御系の知識**で、operation の payload は
`command` / `timeout` / `lease` しか運んでいなかった。

## 決定

1. **保全コマンドは制御系が組み立て、payload の `preserve` で worker へ運ぶ**。worker はゲスト内の配置も
   認証も wip の命名規則も知らない。worker 側で `~/work/*/runtime.env` を探して当てにいく案は、
   当て方が脆く wip 名も作れないので採らない。`preserve` は省略可・空文字は「保全なし」で、
   `preserve` を載せない古い制御系とも組み合わせて動く。
2. **runner は全部の guest-exec に同じ `preserve` を載せる**（`MacRun.preserve_command()`）。
   どの operation の最中に取り消されても保全できる必要があるため、agent 工程だけに絞らない。
   wip 名と refspec は `workflow/bin/run` の `preserve()` と**同じ規則**にする
   （`kb run --from` の既定ブランチと一致させるため）。`HEAD` ではなく作業ブランチを押すのも同じ理由
   （gates の base 確認で HEAD が detached のことがある）。
   ただし **無条件の force push にはしない**。全部の guest-exec に載せた結果、まだ実装が 1 つも乗っていない
   作業ブランチ（`setup_project` 直後は `origin/<base>` と同じ）でも保全が走るようになり、そのままだと
   **前の run が保全した wip を base まで巻き戻して消す**（ADR-0053 の「取り返すには reflog が要る」事故）。
   押す前に、上書きする相手（origin の wip、無ければ `origin/<base>`）が作業ブランチの**祖先で、かつ
   作業ブランチが先に進んでいる**ことを確かめ、満たすときだけ押す。満たさない回は理由を出して
   終了コード 0 で終わる（停止を妨げない）。push は確かめた sha を渡した `--force-with-lease` で行い、
   確かめてから押すまでの間に wip が動いていたら押さない。
   wip を含まない系統（PR 再開など）で取り消された回は、その回の作業ではなく **wip の側を残す**。
3. **worker は取り消し由来の停止の直前に 1 回だけ走らせる**（`preserveWork`）。対象は `main.go` の
   guest-exec 側の 2 つの停止、すなわち `cmdCtx` が切れた回（取り消し・payload の timeout・制御系を
   60 秒見失った watchdog が混ざる）と、`tart exec` 自体が壊れた回。**どれもゲストを止めて実装を捨てる**ので
   分けない。`lifecycle.go` の停止経路には入れない（決定 1 の知識が無い場面で、失うものも無い）。
4. **保全は停止を妨げない**。push の成否・時間切れにかかわらず `stopGuest()` へ進む。上限は
   `preserveTimeout = 120` 秒で、`lease` 待ちの他 run をそれ以上待たせない。予算は
   `context.Background()` 側に置く。取り消し済みの `ctx` に紐づけると保全は始まる前に死ぬ。
5. **試行と結果を operation のログに残す**。`[preserve] …`（開始）と `[preserve] ok: <出力の末尾 300 バイト>` /
   `[preserve] failed: <出力の末尾 300 バイト>` の 1 行ずつ。成功のときも末尾を残すのは、決定 2 の
   「押した（`preserved`）」と「押す価値が無いので押さなかった（`skipped: …`）」を言い分けるため。今までは何も残らず、失敗した回の事後追跡ができなかった。
   **コマンド本文（payload）と stdin はログに出さない**（`pull.py` の `log_message` と同じ方針）。

## 帰結

- 取り消し・timeout・watchdog でゲストが止まる回でも、コミット済みの実装は `sandbox/<task>-<wf>-wip` に残る。
  人は `kb run --from` の既定ブランチとしてそのまま拾える。
- 実装がまだ乗っていない回・wip を含まない系統の回は何も押さない。`[preserve] ok: skipped: …` が残るので、
  「保全できた回」と「巻き戻しを避けた回」はログで区別できる。
- 保全が失敗しても worker は詰まらない。失敗の理由は operation のログに残るので、`operation_show` で読める。
- **限界**: `runtime.env` の `GH_TOKEN` は 1 時間で切れる。長い agent 工程の後半で取り消されると push は
  401 で落ちうる。worker は token を発行できないので、ここは best effort のままにしてログに理由を残すだけにする
  （払い出し直しは制御系の仕事で、この決定の外）。
- **限界**: 保全は「コミット済み」の作業しか救えない。未コミットの変更は救えない。
  実装役に 30 分ごとのコミットを課しているのはこのため。
- ゲスト停止が最大 120 秒遅れうる。lease 待ちの他 run はその分待つ。

## 代替案

- **worker が自力で作業ディレクトリを探して push する**: `$SANDBOX_APP_DIR` も wip 名も推測になる。採らない。
- **制御系が停止の前に割り込んで push する**: 取り消しを送った側は worker が busy で入れない
  （ADR-0066 の実測）。これが今の壊れ方そのもの。
- **保全が終わるまでゲストを止めない**: 失敗した回に worker が詰まり、lease が返らない。
  「保全の失敗で worker が詰まらない」を満たさない。
