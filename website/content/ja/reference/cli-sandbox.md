# sandbox CLI

`sandbox/bin/sandbox` は、Mac から Proxmox とゲートウェイに SSH 接続し、VM の貸出や返却を行う CLI です。bash で実装されています。`sandbox/bin/install.sh` を実行すると、`~/.local/bin/sandbox` にコピーされます。

## 5 つの基本操作と ls

```
sandbox take <pj> <task-id>      空き VM を貸し出す（DNS: task-<id>.sb.internal、env 注入）
sandbox ssh <task-id> [cmd...]   dev で入る / コマンド実行（login shell 経由）
sandbox url <task-id>            http://task-<id>.sb.internal:3000
sandbox reset <task-id>          snapshot clean に巻き戻す（貸出継続、env 再注入）
sandbox release <task-id> [--force]  巻き戻して返却（--force: 巻き戻せなくても台帳から消す）
sandbox ls                       プール VM の一覧（貸出先 / IP / 稼働状態）
sandbox status [pj]              プールの台数（定義 / 実体 / 貸出 / 空き）
```

運用補助として `sandbox idle-stop [--hours N] [--dry-run]` があります（使われていない VM を止める。下記）。

| 操作 | 何をするか | 失敗の条件 |
|---|---|---|
| `take` | プロジェクトプールの空き VM を選んで `state.json` に予約（ここまで `state.json.lock` の排他区間。同時 take が同じ VM を選ばない） → `qm rollback clean` → プロジェクトの env と GitHub App トークンを `/run/sandbox/env` に → sb-gw の dnsmasq に登録 → 予約を確定。途中で失敗したら予約を消す | 空きなし、プロジェクトの env なし、App が未インストール |
| `ssh` | `ssh dev@10.77.1.N`（`SB_JUMP` があれば ProxyJump）。cmd は login shell 経由（`/etc/profile.d/sandbox.sh` で env が読まれる） | VM に届かない |
| `url` | `http://task-<id>.<SB_DOMAIN>:<APP_PORT>` を表示 | |
| `reset` | `qm rollback clean` → env 再注入。貸出は継続 | `clean` がない、巻き戻しに失敗（貸出は継続したまま非0で終わる） |
| `release` | reset → DNS 登録を外す → `state.json` から削除。rollback のロック競合は既定で 3 回・10 秒間隔まで待ってリトライし（`SB_ROLLBACK_TRIES` / `SB_ROLLBACK_WAIT`）、失敗は毎回 stderr に出る | 巻き戻しに失敗（非0で終わり `state.json` に残す。次の一手はメッセージに出る）。`--force` を付けると巻き戻せなくても削除する（人が手で直した VM 用） |
| `ls` | `TASK VM VMID IP STATUS SINCE`。TASK は貸出先の task-id（貸出なしは `-`）、STATUS は Proxmox の電源状態（running / stopped）で貸出とは別の軸。返却してもすぐには止まらないので、貸出 0 台でも running が並ぶ。節電で止まっている VM があれば表の後に `[idle-stop] N 台が節電で停止中` の 1 行が付く | |
| `status` | `PJ DEFINED ACTUAL LENT FREE`。DEFINED は設定の定義台数（`SANDBOX_POOL_PER_PJ`、既定 3）、ACTUAL は Proxmox に実在する VM の台数、LENT は台帳の貸出台数、FREE は `ACTUAL - LENT`（0 で下げ止まり）。引数の `pj` でその 1 行だけ出す | |

`sandbox ls` の出力例:

```
TASK     VM             VMID   IP           STATUS    SINCE
204      sb-kumitate-01 9204   10.77.1.4    running   2026-09-06T12:00:07+09:00
-        sb-kumitate-02 9205   10.77.1.5    running
```

「定義台数」（設定の値）と「実体台数」（Proxmox に実在する VM の数）は別物です。定義だけを見て同時に走らせると、実体が足りないぶんが `take` の「空きなし」になります。`sandbox status` は 2 つを分けて出します:

```
PJ             DEFINED  ACTUAL  LENT  FREE
aifactory      3        2       2     0
kumitate       3        3       0     3
```

空きなしの `take` は内訳と次の一手を出します:

```
[error] pj=aifactory に空きなし: 定義 3 台・実体 2 台・貸出 2 台（未構築 1 台 / clean 無し 0 台）。返却を待つ（sandbox ls）か、proxmox/40-pool.sh aifactory 1 で足してください
```

`clean 無し` は snapshot `clean` が無くて飛ばした台数です。この状態は `sandbox ls` からは分からないので、コンソールと `sandbox status` の「空き」には数えたまま出ます。

## 使われていない VM を止める（`idle-stop`）

貸し出されていないプール VM は、CPU とメモリを占有したまま起動し続けます。`sandbox idle-stop` は、**貸出中でなく、最後に使われてから一定時間が経った**プール VM を止めます（ADR-0033）。制御系 LXC の systemd timer `aifactory-idle-stop.timer` が 15 分ごとに呼びます。

```
sandbox idle-stop [--hours N] [--keep K] [--dry-run]
```

| 項目 | 内容 |
|---|---|
| 対象 | `sb-<t>-<pj>-NN` の qemu VM で、いま running のもの。制御系の `ctl` / `gw` は LXC なので対象外。`-base` / `-tpl-` も除く |
| 候補の条件 | 貸出台帳（`state.json`）に vmid が無く、最終利用から `N` 時間以上経っている |
| 止める条件 | 候補を最終利用の古い順に並べ、新しい方から `K` 台を残して、それより古いものだけ止める（候補が `K` 台以下なら止めない。ADR-0035） |
| `N` の既定 | `--hours` > `SB_IDLE_STOP_HOURS` > `24`。`0` で無効。`pj/<pj>.env` に書けば PJ 別に上書きできる（常時起動にしたい PJ 用） |
| `K` の既定 | `--keep` > `SB_IDLE_STOP_KEEP` > `10`。`0` で候補を全部止める。全体設定のみ（PJ 別にはしない） |
| 最終利用 | `~/.config/sandbox/last-used.json`（`take` / `reset` / `release` / `reinject` のたびに更新）。記録が無ければ VM の `uptime` で代用し、それも取れなければ**止めない** |
| 止め方 | `status/shutdown`（timeout 120 秒。guest agent / ACPI）→ 止まらなければ `status/stop` |
| 記録 | `~/.config/sandbox/idle-stop.json`（`hours` / `keep` / `last_run` / `stopped[]` / `candidates[]`＝候補のまま起動している VM）。`sandbox ls` の脚注、コンソール、MCP `sandbox_status` がここを読む |
| `--dry-run` | 判定だけ出して止めない。記録も書かない |

出力例:

```
[idle-stop] sb-main-kumitate-02 (9205): shutdown（最終利用 51h12m 前、24h 超）
[idle-stop] sb-main-kumitate-01 (9204): 候補だが残す（最終利用 26h03m 前。新しい方から 10 台の内）
[idle-stop] 対象 13 台: 候補 11（停止 1 / 残す 10。足切り 10 台）/ 貸出中 1 / 未経過 1 / 不明 0
```

**起こすためのコマンドはありません**。止まった VM は次の `take` が自動で起動します（`rollback` が ssh の起動を待ちます）。起動に 30〜60 秒ほど余分にかかり、そのあいだ次の 1 行がログに出ます:

```
[start] vm 9205: 停止中だったので起動した（42 秒）
```

判定から停止の完了までは `state.json.lock` を 1 台ずつ保持します。`take` が予約した直後の VM を止めてしまわないためで、そのぶん同時に走る `take` の待ちは最大 `SB_LOCK_WAIT` 秒（既定 150）まで伸びます。

## 認証情報の管理と更新

```
sandbox token set <pj|global> [claude|gh]   トークンを対話入力して保存（既定 claude）。PJ 別ファイルに書く
sandbox token rotate [claude|gh]            1 回の入力で global・全 PJ・ctl.env を差し替え（console restart と reinject --all まで）
sandbox token show [pj]                     どのトークンが効いているか（マスク表示・発行からの日数・ホスト種別）
sandbox token clear <pj|global> [claude|gh] トークンを消す
sandbox keys list|add|set|rm|token          Claude の鍵プール（制御系の keys.json）。名前とフラグを付けた鍵を並べ、take が系統ごとに 1 本選ぶ
sandbox reinject <task-id>|--all            貸出中の VM に現在の設定を再注入（巻き戻しなし。鍵の差し替え用）
sandbox gh-app status|token <pj>|refresh    GitHub App: 設定確認 / <pj> の installation token を表示 / 貸出中 VM の GH_TOKEN を全部払い出し直す
```

### token

| コマンド | 書き先 |
|---|---|
| `token set <pj>` | `~/.config/sandbox/pj/<pj>.env` の `CLAUDE_CODE_OAUTH_TOKEN` |
| `token set <pj> claude:<fable\|opus\|sonnet\|haiku>` | 同じファイルの `CLAUDE_CODE_OAUTH_TOKEN_<系統>`。runner が step のモデル名から系統を選び、その鍵があればそれで `claude -p` を起動する（無ければ `CLAUDE_CODE_OAUTH_TOKEN`）。Fable と Opus を別の鍵で動かすときに使う。`rotate` / `clear` も同じ指定を受ける |
| `token set <pj> gh` | 同 `GH_TOKEN`（App 未設定時のフォールバック） |
| `token set global` | `~/.config/sandbox/env`（全プロジェクトの既定） |
| `token rotate [claude\|gh]` | `~/.config/sandbox/env` と、その鍵を持つ `pj/*.env` 全部と、`~/.config/aifactory/ctl.env` |
| `token show [pj]` | 効いているトークンの出どころとマスク表示、保存からの日数、実行ホストが制御系かどうか。鍵プールがあれば本数と系統ごとの候補数も 1 行 |

期限切れの差し替えは制御系（`ctl.env` のあるホスト）で `sandbox token rotate` を 1 回。更新した場所を一覧で出したあと、`ctl.env` を更新したときは `aifactory-console` を再起動し（`sudo -n` が通らなければコマンドを表示）、貸出中の VM があれば `reinject --all` まで行います（ADR-0029）。VM の中で動いている `claude` は、従来どおり VM 内で再起動が要ります。

### keys

制御系の `~/.config/sandbox/keys.json`（600）に名前を付けた Claude の鍵を並べておくと、`take` / `reset` / `reinject` が系統ごとに 1 本ずつ選んで VM に渡します（ADR-0043）。鍵ごとに「fable に使う」「fable 以外（Opus / Sonnet / Haiku）に使う」の 2 つのフラグがあり、Fable 用の契約と Opus 用の契約を分けられます。

| コマンド | 何をするか |
|---|---|
| `keys add <名前> [--fable] [--other] [--note …]` | 鍵を足す。値は対話入力（エコー無し）か標準入力から受け取る。フラグは少なくとも一方が要る。名前は `[A-Za-z0-9._-]` の 1〜40 文字で一意 |
| `keys list [--json]` | 名前・フラグ・使うかどうか・トークンの末尾 4 文字・発行日・最終利用・使用回数・その鍵を使っている貸出。値は出さない |
| `keys set <名前> [--fable=on\|off] [--other=on\|off] [--enable\|--disable] [--note …]` | フラグ・使うかどうか・メモを変える。使わない設定にしたときは、その鍵を使っている貸出への `reinject` を案内する |
| `keys token <名前>` | 値だけ差し替える（名前・フラグ・使用回数はそのまま）。貸出中の VM には `reinject` で反映する |
| `keys rm <名前> [--force]` | 消す。貸出中の task が使っていれば `--force` が要る |

選び方は「その task が前に使った鍵（まだ使える設定なら）→ 無ければ、フラグの合う鍵のうち最後に使ってから最も時間が経ったもの」です。候補が 1 本も無い系統は、これまでどおり `pj/<pj>.env` と `env` の鍵に落ちます（プールが空なら挙動は今までと同じ）。VM には既存の系統別変数（`CLAUDE_CODE_OAUTH_TOKEN_FABLE` / `_OPUS` / `_SONNET` / `_HAIKU`）で渡り、名前だけが `CLAUDE_KEY_NAME_<系統>` と貸出台帳に残るので、run のログは `key=CLAUDE_CODE_OAUTH_TOKEN_OPUS (pool: opus-a)` になります。

`sandbox token rotate` はプールを触りません（`env` と `ctl.env` の鍵だけを差し替えます）。

### gh-app

| コマンド | 何をするか |
|---|---|
| `gh-app status` | App の ID、権限一覧（必要: contents / pull_requests write、metadata / actions read。任意: checks read）、installation 一覧、プロジェクトごとの token 可否、変更用 URL |
| `gh-app token <pj>` | そのプロジェクトのリポジトリ限定の installation token を払い出して表示（1 時間） |
| `gh-app refresh` | 貸出中の全 VM の `GH_TOKEN` を払い出し直す。launchd が 45 分ごとに呼ぶ |

CLI は、必要な権限のうち GitHub App に許可されているものを要求します。App に権限を追加し、インストール先で承認すると、CLI を変更せずに新しいトークンへ反映されます。

## 設定

| ファイル | 内容 |
|---|---|
| `~/.config/sandbox/env` | `SB_TENANT`（既定 `main`。`SB_PREFIX` = `sb-<t>`、`SB_DOMAIN` = `<t>.sb.internal`、`SB_POOL` = `sb-<t>` を導く）/ `PVE_HOST`（Proxmox ホストの ssh エイリアス。ssh モードで必須、既定なし）または `PVE_API_URL` + `PVE_API_TOKEN`（API モード。テナントのプール限定のトークン。`PVE_API_CA` か `PVE_API_INSECURE=1`。ADR-0017）/ `GW_SSH`（ゲートウェイ LXC への ssh 先。必須、既定なし）/ `SB_KEY` / `SB_DOMAIN` / `APP_PORT` / `SB_JUMP` / `SB_POOL_NET`（既定 `10.77.1`）/ `SB_POOL_BASE`（既定 `9200`）/ `SB_IDLE_STOP_HOURS`（使われていない VM を停止候補にするまでの時間。既定 24、`0` で無効）/ `SB_IDLE_STOP_KEEP`（候補のうち起動したまま残す台数。既定 10）。ひな形 `sandbox/templates/env.example` |
| `~/.config/sandbox/tenants/<t>.env` | 別テナントの設定（メンテナの手元）。`SB_TENANT=<t>` で `sandbox` と `proxmox/run.sh` が読む。状態は `<t>.state.json`、PJ 別設定は `<t>.pj/` |
| `~/.config/sandbox/pj/<pj>.env` | `GH_REPO=owner/name`、`CLAUDE_CODE_OAUTH_TOKEN`、任意で `CLAUDE_CODE_OAUTH_TOKEN_FABLE` / `_OPUS` / `_SONNET` / `_HAIKU`（モデル系統別の鍵）、（フォールバック用 `GH_TOKEN`）、`SB_IDLE_STOP_HOURS`（この PJ だけ上書き） |
| `~/.config/sandbox/gh-app/app.env` + `private-key.pem` | GitHub App。`sandbox/bin/gh-app-setup` が作る |
| `~/.config/sandbox/state.json` | 貸出台帳。`{ "<task-id>": {"vmid", "name", "ip", "pj", "since"} }` |
| `~/.ssh/conf.d/aifactory/config` | `gw.*.sb.internal` / `ctl.*.sb.internal` / `*.sb.internal` / `10.77.*` の ssh 設定。ひな形 `ssh_config.example` |

設定は `env`（全体の既定値）、`pj/<pj>.env`（プロジェクト別）、`SANDBOX_CLAUDE_TOKEN` / `SANDBOX_GH_TOKEN`（今回だけの指定）の順に読み込み、後の値で上書きします。

シェルに export された `CLAUDE_CODE_OAUTH_TOKEN` / `GH_TOKEN` は使いません。過去に、これらの値が意図せずプロジェクト設定を上書きしたためです。

## VM に注入されるもの

`/run/sandbox/env`（tmpfs、dev 所有、`umask 077`）:

```
TASK_ID=204
SANDBOX_PJ=kumitate
SANDBOX_HOST=task-204.sb.internal
CLAUDE_CODE_OAUTH_TOKEN=…
GH_TOKEN=ghs_…
GH_REPO=akkijp/kumitate
GH_TOKEN_EXPIRES_AT=2026-09-06T03:55:00Z
```

ログイン時に `/etc/profile.d/sandbox.sh` がこのファイルを読み込みます。同時に `SANDBOX_APP_DIR` と PATH も設定します。

## Proxmox 側スクリプト {#proxmox}

`sandbox/proxmox/run.sh <script> [args]` は、`PVE_HOST` で指定した Proxmox ホストに SSH 接続し、`_tenant.sh`（テナントの名前・番号の導出）を前に付けてスクリプトを実行します。`PVE_HOST` は必須です。Mac の公開鍵と、あれば制御系 LXC の鍵が `SB_PUBKEY` として渡されます。`SB_TENANT=<t>` を付けると `~/.config/sandbox/tenants/<t>.env` を読み、そのテナントの値で動きます（ADR-0017）。

テナントの設計値は次の環境変数で指定できます。指定しなければ既定値（`main` テナント）です。

| 変数 | 意味 | 既定 |
|---|---|---|
| `SB_TENANT` | テナントの slug（`[a-z0-9]{1,6}`）。名前・zone / vnet・firewall group・プール・DNS ドメインを導出する（規則: すべてに `sb` と `<t>`） | `main` |
| `SB_NET` | sandbox ネットワークの /16 プレフィックス（テナントごとに変える） | `10.77` |
| `SB_VMID_BASE` | VMID 帯の起点（gw = +0、ctl = +1、base = +100、PJ テンプレート = +110..、プール = +200..。テナントごとに変える） | `9000` |
| `SB_NODE` | SDN zone を載せる Proxmox ノード名 | ホストの hostname |
| `SB_GW_CT` / `SB_CTL_CT` / `SB_BASE_VMID` / `SB_TPL_BASE` / `SB_POOL_BASE` | 帯の中の個別の番号（普通は導出のまま） | `9000` / `9001` / `9100` / `9110` / `9200` |
| `SB_ZONE` / `SB_VNET` / `SB_PREFIX` / `SB_FW_GROUP` / `SB_POOL` / `SB_DOMAIN` | 導出される名前の個別上書き | `sbmain` / `vnmain` / `sb-main` / `sb-main` / `sb-main` / `main.sb.internal` |

| スクリプト | 何を作る |
|---|---|
| `05-tenant.sh [create\|token\|adopt\|show]` | リソースプール `sb-<t>`、ロール `AifactorySandbox`、ユーザー `sb-<t>@pve`、プール限定の ACL。`adopt` は既存の VM / CT をプールに入れる。`token` は手元用の API トークンを表示 |
| `10-sdn.sh` | SDN zone `sb<t>` / vnet `vn<t>`（`SB_NET.0.0/16`、SNAT） |
| `20-gateway-lxc.sh` | `sb-<t>-gw` LXC（dnsmasq、tailscaled、VM 発の転送を落とす systemd unit） |
| `25-control-lxc.sh` | 制御系 LXC `sb-<t>-ctl`（checkout・workspace・`sandbox` CLI（API モード）・console + `/docs/`・gh-refresh timer・runner の道具。systemd 常駐）。API トークンを発行して中に書く。env: `AIFACTORY_REPO_URL` `AIFACTORY_REF` |
| `30-base-template.sh create` | `sb-base` 9100（cloud image + cloud-init → `31-provision-base.sh` → template） |
| `31-provision-base.sh` | base 層の中身（VM 内で実行） |
| `32-pj-template.sh` | `sb-tpl-<pj>` 911x（base から clone → プロジェクトの `provision.sh`（`workspace/projects/<pj>/` → `examples/projects/<pj>/`）→ template）。env: `GH_TOKEN` `TPL_VMID` `PJ` |
| `40-pool.sh <pj> <n>` | プール 92xx（linked clone × n → cloud-init で公開鍵（メンテナ + 制御系）→ 起動 → `clean` スナップショット）。env: `TPL_VMID` |
| `45-pool-keys.sh [pj]` | 既存のプール VM に公開鍵を後から入れる（guest agent で `authorized_keys` に追記 → `clean` 取り直し）。制御系 LXC を後から足したとき用。env: `LENT` |
| `50-firewall.sh` | datacenter ファイアウォール + group `sb-<t>`（VM 用）と `sb-<t>-ctl`（制御系用。`cluster.fw` の自分の節だけ書き換える）+ プールの全 VM にファイアウォール=1 + `clean` 取り直し + sb-gw の FORWARD DROP。env: `LENT`（貸出中 VMID を飛ばす） |

## gh-app-setup

`sandbox/bin/gh-app-setup [app-name]` は、マニフェストを使って GitHub App を作成し、App ID と秘密鍵を `~/.config/sandbox/gh-app/` に保存します。既定の名前は `aifactory-sandbox` です。名前は GitHub 全体で重複しないものを指定してください。既定の権限は contents / pull_requests の write と、metadata / actions / checks の read です。

## launchd

`sandbox/templates/launchd/com.aifactory.sandbox.gh-refresh.plist` を `~/Library/LaunchAgents/` にコピーし、`launchctl load` で登録します。登録後は、45 分ごとに `sandbox gh-app refresh` が実行されます。

実行するのは `~/.local/bin/sandbox` にコピーしたファイルです。シンボリックリンクでは、macOS のアクセス制御（TCC）によって実行できないためです。
