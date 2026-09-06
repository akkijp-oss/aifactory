# sandbox CLI

`sandbox/bin/sandbox` は、Mac から Proxmox とゲートウェイに SSH 接続し、VM の貸出や返却を行う CLI です。bash で実装されています。`sandbox/bin/install.sh` を実行すると、`~/.local/bin/sandbox` にコピーされます。

## 5 つの基本操作と ls

```
sandbox take <pj> <task-id>      空き VM を貸し出す（DNS: task-<id>.sb.internal、env 注入）
sandbox ssh <task-id> [cmd...]   dev で入る / コマンド実行（login shell 経由）
sandbox url <task-id>            http://task-<id>.sb.internal:3000
sandbox reset <task-id>          snapshot clean に巻き戻す（貸出継続、env 再注入）
sandbox release <task-id>        巻き戻して返却
sandbox ls                       貸出状況
```

| 操作 | 何をするか | 失敗の条件 |
|---|---|---|
| `take` | `state.json` からプロジェクトプールの空き VM を選ぶ → `qm rollback clean` → プロジェクトの env と GitHub App トークンを `/run/sandbox/env` に → sb-gw の dnsmasq に登録 → `state.json` に記録 | 空きなし、プロジェクトの env なし、App が未インストール |
| `ssh` | `ssh dev@10.77.1.N`（`SB_JUMP` があれば ProxyJump）。cmd は login shell 経由（`/etc/profile.d/sandbox.sh` で env が読まれる） | VM に届かない |
| `url` | `http://task-<id>.<SB_DOMAIN>:<APP_PORT>` を表示 | |
| `reset` | `qm rollback clean` → env 再注入。貸出は継続 | `clean` がない |
| `release` | reset → DNS 登録を外す → `state.json` から削除。rollback のロック競合は待ってリトライ | |
| `ls` | `TASK VM VMID IP STATUS SINCE` | |

`sandbox ls` の出力例:

```
TASK     VM             VMID   IP           STATUS    SINCE
204      sb-kumitate-01 9204   10.77.1.4    running   2026-09-06T12:00:07+09:00
-        sb-kumitate-02 9205   10.77.1.5    running
```

## 認証情報の管理と更新

```
sandbox token set <pj|global> [claude|gh]   トークンを対話入力して保存（既定 claude）。PJ 別ファイルに書く
sandbox token show [pj]                     どのトークンが効いているか（マスク表示）
sandbox token clear <pj|global> [claude|gh] トークンを消す
sandbox reinject <task-id>|--all            貸出中の VM に現在の設定を再注入（巻き戻しなし。鍵の差し替え用）
sandbox gh-app status|token <pj>|refresh    GitHub App: 設定確認 / <pj> の installation token を表示 / 貸出中 VM の GH_TOKEN を全部払い出し直す
```

### token

| コマンド | 書き先 |
|---|---|
| `token set <pj>` | `~/.config/sandbox/pj/<pj>.env` の `CLAUDE_CODE_OAUTH_TOKEN` |
| `token set <pj> gh` | 同 `GH_TOKEN`（App 未設定時のフォールバック） |
| `token set global` | `~/.config/sandbox/env`（全プロジェクトの既定） |
| `token show [pj]` | 効いているトークンの出どころとマスク表示 |

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
| `~/.config/sandbox/env` | `PVE_HOST`（Proxmox ホストの ssh エイリアス。必須、既定なし）/ `GW_SSH`（ゲートウェイ LXC への ssh 先。必須、既定なし）/ `SB_KEY` / `SB_DOMAIN` / `APP_PORT` / `SB_JUMP` / `SB_POOL_NET`（既定 `10.77.1`）/ `SB_POOL_BASE`（既定 `9200`）。ひな形 `sandbox/templates/env.example` |
| `~/.config/sandbox/pj/<pj>.env` | `GH_REPO=owner/name`、`CLAUDE_CODE_OAUTH_TOKEN`、（フォールバック用 `GH_TOKEN`） |
| `~/.config/sandbox/gh-app/app.env` + `private-key.pem` | GitHub App。`sandbox/bin/gh-app-setup` が作る |
| `~/.config/sandbox/state.json` | 貸出台帳。`{ "<task-id>": {"vmid", "name", "ip", "pj", "since"} }` |
| `~/.ssh/conf.d/aifactory/config` | `sb-gw` / `*.sb.internal` / `10.77.*` の ssh 設定。ひな形 `ssh_config.example` |

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

`sandbox/proxmox/run.sh <script> [args]` は、`PVE_HOST` で指定した Proxmox ホストに SSH 接続し、スクリプトを実行します。`PVE_HOST` は必須です。Mac の公開鍵は `SB_PUBKEY` として渡されます。

ネットワークと VMID は、次の環境変数で指定できます。指定しなければ、各スクリプトの既定値が使われます。

| 変数 | 意味 | 既定 |
|---|---|---|
| `SB_NODE` | SDN zone を載せる Proxmox ノード名 | ホストの hostname |
| `SB_NET` | sandbox ネットワークの /16 プレフィックス | `10.77` |
| `SB_GW_CT` | ゲートウェイ LXC の CT ID | `9000` |
| `SB_BASE_VMID` | base テンプレートの VMID | `9100` |
| `SB_POOL_BASE` | プール VM の VMID の起点（Mac 側 `SB_POOL_BASE` とそろえる） | `9200` |

| スクリプト | 何を作る |
|---|---|
| `10-sdn.sh` | SDN zone `sb` / vnet `sbnet`（10.77.0.0/16、SNAT） |
| `20-gateway-lxc.sh` | `sb-gw` LXC 9000（dnsmasq、tailscaled、VM 発の転送を落とす systemd unit） |
| `30-base-template.sh create` | `sb-base` 9100（cloud image + cloud-init → `31-provision-base.sh` → template） |
| `31-provision-base.sh` | base 層の中身（VM 内で実行） |
| `32-pj-template.sh` | `sb-tpl-<pj>` 911x（base から clone → プロジェクトの `provision.sh`（`workspace/projects/<pj>/` → `examples/projects/<pj>/`）→ template）。env: `GH_TOKEN` `TPL_VMID` `PJ` |
| `40-pool.sh <pj> <n>` | プール 92xx（linked clone × n → 起動 → `clean` スナップショット）。env: `TPL_VMID` |
| `50-firewall.sh` | datacenter ファイアウォール + group `sandbox` + 全 VM にファイアウォール=1 + `clean` 取り直し + sb-gw の FORWARD DROP。env: `LENT`（貸出中 VMID を飛ばす） |

## gh-app-setup

`sandbox/bin/gh-app-setup [app-name]` は、マニフェストを使って GitHub App を作成し、App ID と秘密鍵を `~/.config/sandbox/gh-app/` に保存します。既定の名前は `aifactory-sandbox` です。名前は GitHub 全体で重複しないものを指定してください。既定の権限は contents / pull_requests の write と、metadata / actions / checks の read です。

## launchd

`sandbox/templates/launchd/com.aifactory.sandbox.gh-refresh.plist` を `~/Library/LaunchAgents/` にコピーし、`launchctl load` で登録します。登録後は、45 分ごとに `sandbox gh-app refresh` が実行されます。

実行するのは `~/.local/bin/sandbox` にコピーしたファイルです。シンボリックリンクでは、macOS のアクセス制御（TCC）によって実行できないためです。
