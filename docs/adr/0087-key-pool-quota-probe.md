# ADR 0087: 鍵プールの残量（利用枠）は制御系が定期的に観測し、「鍵」画面と `keys_list` に窓ごとの残り %・回復時刻・ペースを出す

- 状態: Accepted（ADR-0044 / ADR-0060 の鍵プールに観測を足す。鍵の選び方（ADR-0044 の `last_used` 最古）は変えない）
- 日付: 2026-09-16
- メンテナの判断: 「鍵のどのモデルがあとどのくらい使えるのか、という量が表示できる仕組みを取り込みたい。コストを掛けてもいいから品質を求める」

## 状況

鍵プール（`keys.json`）の「鍵」画面には、鍵ごとの用途・有効・登録日・起動回数・使用中のチケットが並ぶが、**その鍵があとどれだけ使えるか**は分からなかった。分かるのは run が `claude -p` に拒否されて一時停止した後（ADR-0043）で、「そろそろ切れる」「この鍵はもう空」「Fable の枠だけ先に尽きた」を事前に読む手段が無い。

Anthropic のサブスクリプション鍵（`claude setup-token` の OAuth 鍵）は、`POST /v1/messages` の応答ヘッダに利用枠の状態を返す。姉妹プロジェクト（akkijp/aix）が 2026-08 に実測して次を確かめている。

| 窓 | ヘッダ | 意味 |
|---|---|---|
| 5 時間枠 | `anthropic-ratelimit-unified-5h-{utilization,reset,status}` | 全モデル共通の短期の枠 |
| 7 日枠（全体） | `anthropic-ratelimit-unified-7d-*` | 週次の枠 |
| 7 日枠（Fable） | `anthropic-ratelimit-unified-7d_oi-*` | **Fable 専用の週次の枠。`model` が Fable のときにしか返らない** |

- `utilization` は 0.0〜1.0 の使用率、`reset` は unix epoch 秒の回復時刻、`status` は `allowed` / `allowed_warning` / `rejected`。あわせて `unified-status`（全体）と `unified-representative-claim`（いま最も逼迫している窓）、`unified-overage-*`（超過の可否）が付く。
- `count_tokens` は無課金だが**ヘッダを返さない**。本物の messages を `max_tokens: 1` で叩く（入出力あわせて十数トークン）。
- 429 の応答にもヘッダは載る（枯渇した鍵ほど読む価値がある）。401 / 403 は鍵切れの合図になる。
- 窓の「いつから」は返らないが、窓長は既知（5 時間 / 7 日）なので **始点 = reset − 窓長**で確定できる。始点で残量 100% なのは窓の定義上の事実である。

aifactory の鍵は Fable 用と Opus / Sonnet 用に分かれる（ADR-0044）ので、「全体の 7 日枠はまだあるのに Fable の枠だけ先に尽きる」を見落とすと、計画工程（Fable 固定）で run が止まる。安いモデルだけで観測すると Fable の枠は永久に見えない。

## 決定

1. **観測は制御系の `lib/aifactory_keys_quota.py` が行う。** `probe` が有効な鍵を 1 周叩き、応答ヘッダの 3 つの窓を読む。鍵の値は `keys.json` から読んで Anthropic に送るだけで、標準出力・ログ・DB・例外文には名前と末尾 4 文字より先を出さない（ADR-0044 の 7 と同じ約束。値を読む区画が `sandbox` の鍵プール区画・`inject_env` に加えてもう 1 つ増える）。
2. **2 つの周期で叩く。** 安いモデル（既定 `claude-haiku-4-5`。`AIFACTORY_KEYS_PROBE_MODEL`）で 5 分ごとに 5h / 7d を取り、Fable 許可（`allow.fable`）の鍵は前回の全窓観測から 15 分（`AIFACTORY_KEYS_PROBE_FULL_INTERVAL_S`）以上経っていれば Fable（既定 `claude-fable-5-1`。workflow の plan 工程と同じ ID。`AIFACTORY_KEYS_PROBE_FULL_MODEL`）で叩いて 7d_oi も取る。Fable 許可の無い鍵は常に安いモデル（その契約に Fable の枠は無い）。**安いモデルの回は 7d_oi を触らない**（None で上書きすると 15 分ごとの観測値が 5 分ごとの回に消える）。
3. **記録は `keys.json` の隣の `keys-quota.db`（SQLite・600。テナントは `<t>.keys-quota.db`。`SANDBOX_KEYS_QUOTA` で差し替え）。** `key_quota` が鍵ごとの現在値（窓ごとの使用率・reset・status、最終観測、最後の失敗理由）、`key_quota_history` が観測のたびに窓ごと 1 行の時系列。`(鍵, 窓, reset)` ごとに 1 行だけ `status = window_start`・使用率 0 の合成行を `probed = reset − 窓長` に置く（観測開始前に始まった窓も遡って置く。グラフの線が必ず 100% から始まる）。履歴は既定 30 日（`AIFACTORY_KEYS_QUOTA_KEEP_DAYS`）で剪定する。失敗した回は現在値の窓を触らず、理由（`HTTP 401 authentication_error` 等）だけを残す。値を入れ替えた鍵（末尾 4 文字が変わった）と `keys.json` から消えた鍵は、現在値も履歴も捨てる（別の契約の残量を引き継がない）。
4. **定期観測は systemd の timer `aifactory-keys-probe.timer`（5 分ごと）。** `install.sh --systemd` が他の 3 つと同じく登録する。console の「鍵」画面には「いま調べる」があり、`python3 lib/aifactory_keys_quota.py probe --full` をジョブとして起こす（実行中なら二重に起こさない）。ジョブの記録には名前と成否しか出ない。
5. **console / MCP は読むだけ。** `keys_view()` が鍵ごとに `quota` を足す: `windows[]`（`key` = `5h` / `7d` / `7d_oi`、`remaining_pct`、`reset`、`start`、`remain_s`、`elapsed_pct`、`exhausted`、`will_exhaust` と `exhaust_in_s`、`at_window_end`、`status`）、`binding`（いちばん逼迫している窓）、`probed` / `stale`（15 分より古い）/ `error`。要約の規則は lib の `summarize()` の 1 か所に置き、console と MCP `keys_list` と CLI `show` が同じ数字を出す。`GET /api/keys/history?hours=` がグラフ用の履歴、`POST /api/keys/probe` がジョブの起動。
6. **「鍵」画面の読み方は残量の極性（残り %）で統一する。** 鍵ごとのカードに窓ごとのバー（長さ = 残量 %、縦線 = 窓の残り時間 %。バーが線より左なら時間の進みより速く消費している）、残り %、リセットまでの時間、始点 → 終点、判断に効くときだけの一言（枯渇 / このペースだと約 n で枯渇 / 警告域 / 上限到達 / リセット待ち）。残り 10% 以下は赤、25% 以下は橙。一覧の表には「残量」列でいちばん逼迫している窓だけを出す。「残量の推移」は SVG の折れ線（鍵ごとに 1 本。窓と期間を切り替える）で、5 時間枠がリセットのたびに 100% へ戻るのこぎりの形をそのまま見せる。
7. **鍵の選び方は変えない。** `take` は今までどおり `last_used` 最古の鍵を選ぶ。残量で選ぶかどうかは、観測が運用で安定してから別の決定にする。

## 理由

- **なぜ本物の messages を叩くか。** ヘッダはそこにしか付かない。1 回あたり十数トークンで、5 分ごと + 15 分ごとの 2 系統でも 1 鍵 1 日あたり数千トークンに収まる。メンテナの判断は「コストより品質」。
- **なぜ Fable でも叩くか。** aifactory は計画工程を Fable に固定している（2026-09-12 の判断）。Fable の枠は Fable で叩いたときにしか返らないので、安いモデルだけでは「Fable の枠だけ先に尽きる」を検知できない。頻度を 15 分に落として費用を抑える。
- **なぜ SQLite か。** 時系列の剪定と範囲の取り出しが要る。console は既に kanban.db を読み取り専用で開いており、同じ流儀（書くのは 1 か所、console は `mode=ro`）で済む。`keys.json` に書き足さないのは、秘密のファイルの書き手を CLI の鍵プール区画から増やさないため。
- **なぜ lib に置くか（`sandbox` の bash でないか）。** ヘッダの解釈・履歴の合成・要約は bash + jq では読めない大きさになる。`lib/` は runner・console・MCP が共有する Python の置き場（ADR-0062）で、`~/.local/bin/sandbox` はコピーなので checkout の位置を知らない。timer と console は checkout を知っている（`@@REPO@@` / `REPO`）ので、そこから直接呼ぶ。
- **なぜ timer + ジョブで、console の常駐スレッドでないか。** console は「状態を変えるのは CLI 経由」（ADR-0015）で、鍵の値を読まない約束（ADR-0044）。観測を別プロセスにすれば、console の再起動や停止で観測が途切れず、console は読むだけのまま。
- **なぜ要約をサーバー側に置くか。** 「残り %」「逼迫している窓」「ペース」を画面だけで計算すると MCP（AI の運転係）が同じ判断をできない。lib の 1 か所で出せば CLI / 画面 / MCP が同じ数字を言う。リセットまでの時間だけは観測の間も進むので画面で数え直す。

## 結果

- 「鍵」画面の上に「残量（利用枠）」と、下に「残量の推移」が増える。一覧に「残量」列が増える。`keys_list` の `keys[].quota` で AI の運転係も残量を読める（`quota` が null の鍵はまだ観測していない）。
- 制御系では `sandbox/bin/install.sh --systemd` を打ち直して `aifactory-keys-probe.timer` を登録する（`bin/ctl-update` の後）。登録するまでは「鍵」画面の「いま調べる」で観測する。
- `keys.json` の隣に `keys-quota.db`（600）ができる。`sandbox keys rm` で消した鍵の行は次の観測で消える。
- 鍵切れ（401）は run が止まる前に「認証が通りません」として「鍵」画面に出る。
- 対象外: 鍵の選び方（ADR-0044）、intake の鍵（`ctl.env`。プールにないので観測しない）、API 鍵（`x-api-key`。ヘッダの形が違う。プールは OAuth 鍵だけ）。
- テスト: ヘッダの読み方（3 窓 / 429 / 401）、安い回が 7d_oi を消さないこと、始点の合成が 1 行だけなこと、1 周の周期（Fable 許可・間隔・無効・値の入れ替え）、要約（残り % / 逼迫 / ペース / リセット待ち）、console の `/api/keys` `/api/keys/history` `/api/keys/probe`（偽 Anthropic に向けたジョブの往復・記録に値が出ないこと）、MCP `keys_list`、systemd の unit。
