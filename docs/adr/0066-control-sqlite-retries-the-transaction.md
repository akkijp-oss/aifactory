# ADR 0066: 制御系 sqlite のロックは「長く待つ」のではなく「トランザクションをやり直す」

- 状態: Accepted
- 日付: 2026-09-13

## 状況

2026-09-11 の run（`2026-09-11-asura-408-attempt1`）で、implement / gates が緑になった後の review の最中に
runner が `database is locked` で落ちた。同時刻に同じ PJ の run が 3 本走っていた。落ち方は次の順に伝播する。

1. runner（`workflow/lib/macos.py` → `workers/lib/client.py`）は worker の operation の状態を **1 秒ごとに**
   制御系 sqlite（`$AIFACTORY_WORKSPACE/workers/queue.sqlite3`）から直接読む。40 分の agent 工程なら 1 操作で 2400 回。
   同じファイルを pull server（worker からの heartbeat / event / complete）も開くので、run が 3 本なら接続は 6 本を超える。
2. `Client.execute` の `except BaseException: self.store.cancel(op)` が、**制御系 DB の例外でも** op を取り消していた。
3. 取り消しは heartbeat で worker に伝わり、worker は guest-exec を止められないので**ゲストごと停止**する
   （`workers/cmd/aifactory-worker/main.go` の `cmdCtx.Err() != nil` 分岐）。
4. Mac backend は human 落ちで wip を push していなかった（`MacRun.main` の except は `preserve()` を呼んでいなかった）ため、
   ゲスト内のコミット済み・未 push の実装が回収できずに消えた。停止ゲストを起こす operation も無く、約 45 分の再実装になった。

起票時の「`busy_timeout` が repo に 1 件も無い」は事実だが、`sqlite3.connect(path, timeout=10)` は
`PRAGMA busy_timeout=10000` と同じものなので、**同じ値の PRAGMA を足しても何も変わらない**（実測で確認した）。
つまり原因は「待っていなかった」ではなく「**10 秒待っても取れなかった**」である。sqlite の busy 待ちには
公平性が無く、既定の rollback journal では読みと書きが排他なので、毎秒書く接続が 6 本あれば
運の悪い 1 本が待ち切れないことは起こりうる。

さらに、その 10 秒は worker 側の HTTP client の待ち（10 秒。`main.go`）と同じ長さで、
busy 待ちを使い切った要求は worker から見れば必ず時間切れになっていた。

## 決定

1. **1 文を長く待たせるのではなく、トランザクションごと短くやり直す**。`Store` に `tx(fn)` を置き、
   `fn(db)` を 1 トランザクションで回す。`OperationalError` の文面が locked / busy のときだけ、
   予算（`LOCK_RETRY_BUDGET_S = 8` 秒）と回数（`LOCK_RETRY_ATTEMPTS = 6`）の内側で指数 backoff でやり直す。
   1 文の待ち（`BUSY_TIMEOUT_MS = 3000`）は残り予算で頭を押さえ、総時間が予算を超えないようにする。
   予算は worker の HTTP client の 10 秒より短くする（長く抱えると worker が見切って叩き直し、競合が増える）。
2. **業務エラー（`Error`）はやり直さない**。答えが変わらないので待ち時間を捨てるだけになる。
   ロック以外の `OperationalError`（DB が壊れた等）もそのまま外へ出す。
3. **やり直しても同じ結果になる形にする**。operation ID は `submit` の入口で 1 回だけ決め、やり直しの中で
   作り直さない（作り直すと `one_reserved_worker` の UNIQUE で「worker is busy」になる）。
   stdin の spool は `O_EXCL` のままにし、2 回目に既存ファイルを見つけたときは中身の digest が
   `payload["stdin_sha256"]` と一致する場合だけ受け入れる。
4. **`journal_mode=WAL` にする**。読みが書きと競合しなくなり、runner の毎秒の読みが worker の書きとぶつからない。
   既存の DB は次に開いたときに移行する。移行できない置き場では黙って従来のまま続ける（やり直しだけで凌ぐ）。
5. **runner の poll は制御系 DB の locked で operation を取り消さない**。`Client.execute` は読みの locked を
   `POLL_LOCK_BUDGET_S = 120` 秒まで読み直し、超えたときだけ従来どおり失敗する。経過は
   `runs/<run>/worker-operations.log` に残す（次に起きたとき「待ち切れ」か「即時 BUSY」かが読めるように）。
   取り消し自体が失敗しても元の例外を隠さない。
6. **Mac backend は制御系の失敗で human に落ちるときも `preserve()` を試す**。コミット済み・未 push の実装を
   wip ブランチへ push し、`state.json` の `wip_branch` に残す。`release()` は従来どおり呼ばない
   （lease とゲストは人が検査できるように残す。チケット 282 の意図）。ここでの保全は **記録済みの
   `wip_branch` を上書きしない**: 正常経路（`bin/run` の main）が push した後で `release()` が落ちる回もあり
   （`guest-release` が通った後の `release_lease` など）、そこで名前を消すと `kb run --from` の既定ブランチが失われる。
7. **worker（Go）は今回触らない**。「ゲストを止める前に worker が wip を push する」「停止ゲストを起こす
   `guest-start`」は Go の変更が要り、この作業環境では 1 度も動かせない。別のチケットに切る。

## 理由

- 引き金は 1 回のロックで run 全体が落ちることで、その 1 回は「もっと待つ」では減らない（10 秒待って駄目だった）。
  やり直しなら、待ち直している間に他の接続が commit するので、次の試行は素直に通る。
- WAL は競合そのものを減らすが、on-disk 形式が変わるので単独の対策にはしない。やり直し（1）が本命で、WAL は上乗せ。
- 取り消し（5）は「制御系が読めない」と「操作が失敗した」を同じ扱いにしていたのが誤りで、前者で
  ゲストを消す理由は無い。区別の設計そのもの（`Client.execute` が両者を型で分けていない）は本 ADR では直さない。
- 保全（6）は best effort である。同じ worker に別の operation を出す必要があるので、現に予約が残っている間や
  ゲストが既に停止している場合は失敗する。それでも「制御系が落ちた」以外の human 落ち（take の失敗、
  成果物回収の失敗など）では実際に実装を救える。根本的な保全は 7 の worker 側の変更が引き取る。

## 結果（トレードオフ）

- 制御系 DB の呼び出しは最悪 8 秒（従来は 10 秒）かかる。呼び出し 1 回あたりの上限は短くなる。
- WAL により `queue.sqlite3-wal` / `-shm` が横に出る。権限は主 DB の 0600 を継ぐ（`chmod` の後に切り替える）。
  戻すなら `PRAGMA journal_mode=DELETE` を 1 回打つだけでよい。DB は制御系ローカルの置き場が前提。
- `Client.execute` は最大 120 秒、制御系が読めないまま待つ。その間 run の工程は進まないが、operation は生きている。
- ゲスト停止前の push（案 2）と停止ゲストの起動（案 3）は未着手のまま残る。停止ゲストからの復旧は
  従来どおり `guest-release` → `release-lease` → 新規 run のままで、この ADR では変わらない。
- `examples/projects/aifactory/gates.sh` に `unittest-pull` を足した。CI だけが `workers/tests` を回していて、
  実装役が自分の直しをゲートで確かめられなかった。
