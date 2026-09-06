# sandbox CLI

`sandbox/bin/sandbox`（`sandbox/bin/install.sh` で `~/.local/bin/sandbox` に実体コピー）。Mac 側から Proxmox とゲートウェイに ssh して VM を貸し出す bash スクリプト。

## 契約の 5 操作 + ls

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
| `take` | `state.json` から PJ プールの空き VM を選ぶ → `qm rollback clean` → PJ の env と GitHub App トークンを `/run/sandbox/env` に → sb-gw の dnsmasq に登録 → `state.json` に記録 | 空きなし、PJ の env 無し、App が未 install |
| `ssh` | `ssh dev@10.77.1.N`（`SB_JUMP` があれば ProxyJump）。cmd は login shell 経由（`/etc/profile.d/sandbox.sh` で env が読まれる） | VM に届かない |
| `url` | `http://task-<id>.<SB_DOMAIN>:<APP_PORT>` を表示 | |
| `reset` | `qm rollback clean` → env 再注入。貸出は継続 | `clean` が無い |
| `release` | reset → DNS 登録を外す → `state.json` から削除。rollback のロック競合は待ってリトライ | |
| `ls` | `TASK VM VMID IP STATUS SINCE` | |

`sandbox ls` の出力例:

```
TASK     VM             VMID   IP           STATUS    SINCE
204      sb-kumitate-01 9204   10.77.1.4    running   2026-09-06T12:00:07+09:00
-        sb-kumitate-02 9205   10.77.1.5    running
```

## 運用補助（契約外）

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
| `token set global` | `~/.config/sandbox/env`（全 PJ の既定） |
| `token show [pj]` | 効いているトークンの出どころとマスク表示 |

### gh-app

| コマンド | 何をするか |
|---|---|
| `gh-app status` | App の ID、権限一覧（必要: contents / pull_requests write、metadata / actions read。任意: checks read）、installation 一覧、PJ ごとの token 可否、変更用 URL |
| `gh-app token <pj>` | その PJ のリポジトリ限定の installation token を払い出して表示（1 時間） |
| `gh-app refresh` | 貸出中の全 VM の `GH_TOKEN` を払い出し直す。launchd が 45 分ごとに呼ぶ |

要求する権限は「欲しい権限のうち App が持っているもの」。App 側で権限を足して installation が承認すれば、CLI を触らずにトークンに乗る。

## 設定

| ファイル | 内容 |
|---|---|
| `~/.config/sandbox/env` | `PVE_HOST`（Proxmox ホストの ssh エイリアス。必須、既定無し）/ `GW_SSH`（ゲートウェイ LXC への ssh 先。必須、既定無し）/ `SB_KEY` / `SB_DOMAIN` / `APP_PORT` / `SB_JUMP` / `SB_POOL_NET`（既定 `10.77.1`）/ `SB_POOL_BASE`（既定 `9200`）。雛形 `sandbox/templates/env.example` |
| `~/.config/sandbox/pj/<pj>.env` | `GH_REPO=owner/name`、`CLAUDE_CODE_OAUTH_TOKEN`、（フォールバック用 `GH_TOKEN`） |
| `~/.config/sandbox/gh-app/app.env` + `private-key.pem` | GitHub App。`sandbox/bin/gh-app-setup` が作る |
| `~/.config/sandbox/state.json` | 貸出台帳。`{ "<task-id>": {"vmid", "name", "ip", "pj", "since"} }` |
| `~/.ssh/conf.d/aifactory/config` | `sb-gw` / `*.sb.internal` / `10.77.*` の ssh 設定。雛形 `ssh_config.example` |

読む順: `env`（全体既定）→ `pj/<pj>.env`（PJ 別上書き）→ `SANDBOX_CLAUDE_TOKEN` / `SANDBOX_GH_TOKEN`（1 回限りの上書き）。シェルに export された `CLAUDE_CODE_OAUTH_TOKEN` / `GH_TOKEN` は**無視**する（PJ 設定を黙って上書きした事故があったため）。

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

`/etc/profile.d/sandbox.sh` がログイン時に読み、`SANDBOX_APP_DIR` と PATH も設定する。

## Proxmox 側スクリプト {#proxmox}

`sandbox/proxmox/run.sh <script> [args]` が `PVE_HOST`（必須）の Proxmox ホストに ssh してスクリプトを流す入口。Mac の公開鍵を `SB_PUBKEY` として渡す。ネットワークと VMID の設計値は環境変数で渡せる（未設定なら各スクリプトの既定）。

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
| `32-pj-template.sh` | `sb-tpl-<pj>` 911x（base から clone → PJ の `provision.sh`（`workspace/projects/<pj>/` → `examples/projects/<pj>/`）→ template）。env: `GH_TOKEN` `TPL_VMID` `PJ` |
| `40-pool.sh <pj> <n>` | プール 92xx（linked clone × n → 起動 → `clean` スナップショット）。env: `TPL_VMID` |
| `50-firewall.sh` | datacenter firewall + group `sandbox` + 全 VM に firewall=1 + `clean` 取り直し + sb-gw の FORWARD DROP。env: `LENT`（貸出中 VMID を飛ばす） |

## gh-app-setup

`sandbox/bin/gh-app-setup [app-name]`。GitHub App を manifest flow で作り、App ID と秘密鍵を `~/.config/sandbox/gh-app/` に保存する。既定名 `aifactory-sandbox`（GitHub 全体で一意である必要がある）。既定権限は contents / pull_requests write、metadata / actions / checks read。

## launchd

`sandbox/templates/launchd/com.aifactory.sandbox.gh-refresh.plist`。45 分ごとに `sandbox gh-app refresh`。`~/Library/LaunchAgents/` にコピーして `launchctl load`。実体コピーの `~/.local/bin/sandbox` を呼ぶ（シンボリックリンクだと TCC で動かない）。
