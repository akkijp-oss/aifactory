# sandbox 構築手順書（v0）

対象: Proxmox ノード 1 台。本書では ssh エイリアスを `pve1`（例）と書く。実際の値は `~/.config/sandbox/env` の `PVE_HOST`（Mac からの ssh 先）と、Proxmox 側で SDN を載せるノード名 `SB_NODE`（既定はそのホストの hostname）。以下のコマンドは `set -a; . ~/.config/sandbox/env; set +a` で env を読み込んだシェルで打つ（`"$PVE_HOST"` が展開される）。
設計は `README.md`、進捗は `STATUS.md`（雛形）。実機の状態は `$AIFACTORY_WORKSPACE/docs/STATUS.md` に写して書く。**各ステップの完了条件を満たしてから次へ進む**。満たしたらそのチェックを更新する。

凡例: 🤖 = AI が単独で実行できる / 🧑 = 人間（メンテナ）の操作が必要。ここで止まって依頼する。

スクリプトは `proxmox/` に番号順で置いてあり、`proxmox/run.sh <script> [args]` で `PVE_HOST` のホスト上で実行する（Mac の公開鍵を `SB_PUBKEY` として渡す。`PVE_HOST` 未設定なら止まる）。設計値（`SB_NODE` / `SB_NET` = 10.77 / `SB_GW_CT` = 9000 / `SB_BASE_VMID` = 9100 / `SB_POOL_BASE` = 9200）は環境変数で上書きでき、`run.sh` がそのままリモートへ渡す。本書は既定値で書く。手順書とスクリプトが食い違ったら **手順書を直してからスクリプトを直す**（手順書が仕様）。

---

## Step 0. 事前確認 🤖

目的: 壊れた状態の上に作らない。

```bash
# クラスタ（単一ノードなら standalone）が quorate で PVE_HOST に届くこと
ssh "$PVE_HOST" 'pvecm status | grep -E "Quorate|Expected votes|Total votes"; hostname; pveversion'
# 空き資源（RAM は計画分、local-lvm が active）
ssh "$PVE_HOST" 'free -g | awk "/Mem:/{print \$7\" GB avail\"}"; pvesm status | grep local-lvm'
# 9000 番台が未使用であること
ssh "$PVE_HOST" 'qm list; pct list' | grep -E "^\s*9[0-9]{3}" && echo "9xxx 使用中。README の採番を見直す（SB_GW_CT / SB_BASE_VMID / SB_POOL_BASE でずらせる）" || echo "9xxx 空き"
# 同じノードを触っている別作業が無いこと（直近の再起動・ログイン）
ssh "$PVE_HOST" 'uptime; last -n 5 | head -5'
```

Mac 側の準備（無ければ作る）:

```bash
mkdir -p ~/.ssh/conf.d/aifactory ~/.config/sandbox
[ -f ~/.ssh/conf.d/aifactory/sb_ed25519 ] || ssh-keygen -t ed25519 -N "" -C "aifactory-sandbox" -f ~/.ssh/conf.d/aifactory/sb_ed25519
# Mac の ~/.ssh/config が conf.d/* を Include していることを確認
grep -q "conf.d/aifactory" ~/.ssh/config || echo "Include ~/.ssh/conf.d/aifactory/config" >> ~/.ssh/config
cp sandbox/templates/ssh_config.example ~/.ssh/conf.d/aifactory/config
cp sandbox/templates/env.example ~/.config/sandbox/env && chmod 600 ~/.config/sandbox/env
# ~/.config/sandbox/env を開き、PVE_HOST（Proxmox ホストの ssh エイリアス）と GW_SSH を自分の環境に合わせる（既定値は無い）
```

完了条件:
- [ ] Quorate: Yes（単一ノードでも可）、`pveversion` が 9.x
- [ ] 空き RAM が計画分（1 PJ = 3 台 × `VM_MEMORY`、+ テンプレート作業用 1 台）、`local-lvm` active
- [ ] VMID 9000〜9299（既定）が未使用
- [ ] 直近1時間に他者のログイン・再起動が無い（あれば作業者に確認）
- [ ] `~/.ssh/conf.d/aifactory/sb_ed25519(.pub)` と `~/.config/sandbox/env`（`PVE_HOST` / `GW_SSH` 記入済み）がある

### Step 0b. GitHub App（push / PR 権限の払い出し元。ADR-0008） 🤖 → 🧑
```bash
sandbox/bin/gh-app-setup            # ブラウザが開く → 🧑 Create GitHub App を押す → 自動で ~/.config/sandbox/gh-app/ に保存
# 🧑 表示された install リンクで、対象リポジトリの所有アカウント（ユーザー / org）ごとに対象リポジトリを選んで install
sandbox gh-app status               # 全 PJ が OK になること
cp sandbox/templates/launchd/com.aifactory.sandbox.gh-refresh.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.aifactory.sandbox.gh-refresh.plist
```
完了条件:
- [ ] `~/.config/sandbox/gh-app/app.env` と `private-key.pem`（600）
- [ ] `sandbox gh-app status` で全 PJ が OK
- [ ] `launchctl list | grep aifactory` に gh-refresh がある

---

## Step 1. ネットワーク（SDN） 🤖

目的: sandbox 専用の L2 と IP 空間 `10.77.0.0/16`（`SB_NET`.0.0/16）をノードに作り、外向きはホストが NAT する。

```bash
sandbox/proxmox/run.sh 10-sdn.sh
```

スクリプトがやること（手で打つ場合も同じ）:
1. zone `sb`（type simple、nodes = `SB_NODE`、ipam pve）
2. vnet `sbnet`（zone sb）
3. subnet `10.77.0.0/16`（gateway 10.77.0.1、snat 1）
4. `pvesh set /cluster/sdn` で適用

完了条件:
```bash
ssh "$PVE_HOST" 'ip -br a show sbnet'        # 10.77.0.1/16 が付いている
ssh "$PVE_HOST" 'iptables -t nat -S | grep 10.77'   # MASQUERADE 行がある
ssh "$PVE_HOST" 'pvesh get /cluster/sdn/vnets/sbnet/subnets --output-format json' | jq .
```
- [ ] `sbnet` ブリッジに 10.77.0.1/16
- [ ] NAT 規則あり
- [ ] subnet 定義に `snat: 1`, `gateway: 10.77.0.1`

巻き戻し: `pvesh delete /cluster/sdn/vnets/sbnet/subnets/sb-10.77.0.0-16 && pvesh delete /cluster/sdn/vnets/sbnet && pvesh delete /cluster/sdn/zones/sb && pvesh set /cluster/sdn`

---

## Step 2. ゲートウェイ LXC `sb-gw`（VMID 9000 = `SB_GW_CT`） 🤖 → 🧑 → 🤖

目的: Mac の tailnet から `10.77.0.0/16` に届き、`*.sb.internal` の名前が解ける状態にする。VM 側には Tailscale を入れない（ADR-0004）。

### 2a. 作成と基本設定 🤖
```bash
sandbox/proxmox/run.sh 20-gateway-lxc.sh
```
補足: tailnet 承認前に Step 3 以降を進めるときは、Mac から VM へは `ssh -J "$PVE_HOST" dev@10.77.x.x` で入る（Proxmox ホストが `sbnet` 10.77.0.1 を持つ）。CLI は `~/.config/sandbox/env` の `SB_JUMP`（`PVE_HOST` と同じ値。例: `SB_JUMP=pve1`）で同じ経路になる。
やること: Debian 12 テンプレート取得 → `pct create 9000`（512MB / 1 vCPU / 8GB、eth0=vmbr0 DHCP、eth1=sbnet 10.77.0.2/16、unprivileged、onboot）→ `/dev/net/tun` を LXC に通す設定を追記 → 起動 → 中で ip_forward・dnsmasq・Tailscale をインストール → dnsmasq 設定（スクリプト内に埋め込み。`/etc/dnsmasq.d/sandbox.conf`）と `/etc/sandbox/hosts` 配置 → Mac の公開鍵を root に登録。

完了条件:
```bash
ssh "$PVE_HOST" 'pct status 9000; pct exec 9000 -- ip -br a; pct exec 9000 -- systemctl is-active dnsmasq tailscaled'
ssh "$PVE_HOST" 'pct exec 9000 -- dig +short sb-gw.sb.internal @127.0.0.1'   # 10.77.0.2
ssh "$PVE_HOST" 'pct exec 9000 -- curl -sS -o /dev/null -w "%{http_code}\n" https://cloud-images.ubuntu.com/'  # 200（外向き疎通）
```
- [ ] eth0 に LAN の DHCP アドレス（Proxmox ホストと同じサブネットとは限らない。実機の値は `$AIFACTORY_WORKSPACE/docs/STATUS.md` に書く）、eth1 に 10.77.0.2
- [ ] dnsmasq / tailscaled が active
- [ ] 外向き疎通 200

### 2b. Tailscale 認証と route 承認 🧑
```bash
ssh "$PVE_HOST" 'pct exec 9000 -- tailscale up --advertise-routes=10.77.0.0/16 --accept-dns=false --hostname=sb-gw'
```
このコマンドは認証 URL を表示して待つ（LXC 内で `nohup … > /root/tailscale-up.log` にしておくと URL を後から読める）。**人間（メンテナ）に依頼すること**:
1. 表示された URL をブラウザで開いて承認する（これだけは人間。以下 2〜4 は Tailscale API キー `TS_API_KEY` が手元のシェル環境にあれば AI が API で行える）
2. route `10.77.0.0/16` の **Approve**: 管理コンソール > Machines > `sb-gw` > Edit route settings。API なら `POST /api/v2/device/<id>/routes` に `{"routes":["10.77.0.0/16"]}`
3. **ACL（grants）に `10.77.0.0/16` 宛ての許可を足す**（これが無いと route を承認しても `tailscaled` が `Drop: … no rules matched` で落とす）。tailnet の ACL が grants 形式で subnet 宛てを個別に許可している場合は、その grant（例: `src: autogroup:member`）の `dst` に `10.77.0.0/16` を追加する。手順: `GET /api/v2/tailnet/-/acl` で取得・バックアップ → 編集 → `POST …/acl/validate`（`{}` が返れば OK）→ GET の ETag を `If-Match` に付けて `POST …/acl`
4. split DNS: `PATCH /api/v2/tailnet/-/dns/split-dns` に `{"sb.internal":["<sb-gw の Tailscale IP>"]}`（管理コンソールなら DNS > Nameservers > Add nameserver > Custom + Restrict to domain）

実績: 1・2 は人間、3・4 は `TS_API_KEY` があれば AI が API で適用できた。`sb-gw` の Tailscale IP は `$AIFACTORY_WORKSPACE/docs/STATUS.md` に書く

### 2c. 到達確認 🤖
```bash
ssh "$PVE_HOST" 'pct exec 9000 -- tailscale status --self --peers=false'
ping -c 2 10.77.0.2                              # Mac から
dig +short sb-gw.sb.internal                     # Mac から 10.77.0.2 が返る
ssh -i ~/.ssh/conf.d/aifactory/sb_ed25519 root@10.77.0.2 hostname   # sb-gw
```
- [ ] Mac から 10.77.0.2 に ping が通る
- [ ] Mac から `sb-gw.sb.internal` が解ける
- [ ] Mac から root@sb-gw に ssh できる

巻き戻し: `pct stop 9000; pct destroy 9000 --purge`。Tailscale 側は管理コンソールで machine を削除。

---

## Step 3. ベーステンプレート `sb-base`（VMID 9100 = `SB_BASE_VMID`） 🤖

目的: 全 PJ 共通の土台。Ubuntu 24.04 cloud image に開発ツール一式を焼き、テンプレート化する。

### 3a. VM 作成と起動
```bash
sandbox/proxmox/run.sh 30-base-template.sh create
```
やること: cloud image DL → `qm create 9100`（4GB / 2 vCPU / cpu host / virtio-scsi / qemu-agent / serial console）→ import して 40GB に拡張 → cloud-init ドライブ（user `dev`、Mac の公開鍵、ip 10.77.0.100/16 gw 10.77.0.1、nameserver 10.77.0.2、searchdomain sb.internal）→ 起動 → ssh 待ち。

### 3b. 焼き込み
```bash
scp -i ~/.ssh/conf.d/aifactory/sb_ed25519 sandbox/proxmox/31-provision-base.sh dev@10.77.0.100:/tmp/
ssh -i ~/.ssh/conf.d/aifactory/sb_ed25519 dev@10.77.0.100 'sudo bash /tmp/31-provision-base.sh' 2>&1 | tee /tmp/provision-base.log
```
入れるもの（詳細はスクリプト冒頭のコメントと `templates/base/README.md`）:
- OS: タイムゾーン Asia/Tokyo、qemu-guest-agent、unattended-upgrades 無効（再現性優先）
- ビルド依存: build-essential、libpq-dev、libyaml-dev、libssl-dev、zlib1g-dev、libffi-dev、libreadline-dev、libvips、imagemagick、git、curl、jq、unzip、ripgrep
- DB / KVS: PostgreSQL 16（role `dev` superuser、ローカル trust）、Redis
- ブラウザ: Google Chrome stable（system test とスクショ用。snap を避けるため .deb）
- 言語: mise（Ruby / Node の版管理。Ruby 本体は PJ 層で入れる）、Node 22 LTS（mise 経由・グローバル）
- ツール: gh、Claude Code（公式ネイティブインストーラ）
- sandbox 用フック: `/etc/tmpfiles.d/sandbox.conf`（起動時に tmpfs `/run/sandbox` を作る）、`/etc/profile.d/sandbox.sh`（`/run/sandbox/env` があれば読み込む）
- 後片付け: apt clean、`cloud-init clean --logs`、machine-id 初期化、bash 履歴削除

### 3c. テンプレート化
```bash
sandbox/proxmox/run.sh 30-base-template.sh finalize
```
やること: シャットダウン → `qm template 9100`。

完了条件:
```bash
ssh "$PVE_HOST" 'qm config 9100 | grep -E "^(template|name|memory|cores|agent|ide2|scsi0)"'
# 検証用に一時 clone して中身を確認し、消す
ssh "$PVE_HOST" 'qm clone 9100 9199 --name sb-verify --full 0 && qm set 9199 --ipconfig0 ip=10.77.0.199/16,gw=10.77.0.1 >/dev/null && qm start 9199'
sleep 40
ssh -i ~/.ssh/conf.d/aifactory/sb_ed25519 dev@10.77.0.199 'set -e; mise --version; node --version; claude --version; gh --version | head -1; psql -c "select version()" | head -3; redis-cli ping; google-chrome --version; ls -ld /run/sandbox'
ssh "$PVE_HOST" 'qm stop 9199; qm destroy 9199 --purge'
```
- [ ] `template: 1`
- [ ] 検証 clone で mise / node / claude / gh / psql / redis / chrome がすべて応答
- [ ] `/run/sandbox` が dev 所有で存在
- [ ] 検証 clone を削除した

巻き戻し: `qm destroy 9100 --purge`

---

## Step 4. PJ テンプレート `sb-tpl-{pj}`（VMID 911x） 🤖（gh トークンは 🧑 から受領）

目的: 対象 PJ のリポジトリ・依存・DB seed を入れ、アプリが起動する状態を焼く。**PJ が未決なら Step 5 の前にここで止まる**。

前提:
- PJ 定義 `$AIFACTORY_WORKSPACE/projects/{pj}/{project.yml,provision.sh,gates.sh}` がある（`templates/README.md` の雛形か、同梱サンプル `examples/projects/kumitate/` を写す。`AIFACTORY_WORKSPACE` の既定はリポジトリ直下 `workspace/`）
- Mac の `gh auth token` が対象リポジトリを読める。テンプレートには残さない

```bash
# VMID は PJ ごとに 911x を割る（ADR-0007）。割当は $AIFACTORY_WORKSPACE/docs/STATUS.md の PJ 一覧に書く
TPL_VMID=9110 VM_MEMORY=8192 VM_CORES=4 sandbox/proxmox/run.sh 32-pj-template.sh create {pj}   # → 10.77.0.110（9111 なら .111）
GH_TOKEN=$(gh auth token) \
  ssh -i ~/.ssh/conf.d/aifactory/sb_ed25519 dev@10.77.0.110 "GH_TOKEN=$GH_TOKEN bash -s" < "${AIFACTORY_WORKSPACE:-$PWD/workspace}/projects/{pj}/provision.sh"
TPL_VMID=9110 sandbox/proxmox/run.sh 32-pj-template.sh finalize {pj}
```
焼き込みは長い（bundle / pnpm install + seed + 全テスト）ので、VM 内で `nohup … > /tmp/provision.log` にして待つ。

PJ 層で必ず入れるもの:
- `/home/dev/app` にリポジトリ clone（base ブランチ）。`gh auth` の設定は **焼かない**（clone 後に `gh auth logout`、`~/.config/gh` 削除）
- `.ruby-version` / `.node-version` に従い `mise install`
- 依存（`bundle install`、`npm ci` / `pnpm install --frozen-lockfile` など PJ の流儀）
- DB 作成、schema load、seed
- systemd ユニット `sandbox-app.service`（PJ の起動コマンドを `0.0.0.0:3000` で。dev ユーザー、自動起動）
- PJ の lint / test（`gates.sh` と同じ組）が **緑で通る**ことを確認してから焼く。赤のまま焼くと workflow の失敗判定が壊れる（環境依存で必ず赤になるものは info 扱いにする。`templates/README.md` の「ゲートの扱い」）

完了条件:
- [ ] `qm config 9110` が `template: 1`
- [ ] 検証 clone で `curl -s -o /dev/null -w "%{http_code}" http://10.77.0.1xx:3000/` が 2xx / 3xx（トップがリダイレクトする PJ は 3xx）
- [ ] 検証 clone で lint と test が緑
- [ ] 検証 clone に `~/.config/gh` と `GH_TOKEN` が **無い**

---

## Step 5. プールと CLI 🤖

目的: 貸し出せる VM を N 台用意し、Mac から5操作で扱えるようにする。

### 5a. プール作成
```bash
TPL_VMID=9110 VM_MEMORY=8192 VM_CORES=4 sandbox/proxmox/run.sh 40-pool.sh kumitate 3   # 911x は PJ ごと（$AIFACTORY_WORKSPACE/docs/STATUS.md の PJ 一覧）
# 2 PJ を同時に走らせない（VMID の取り合い。OPERATIONS.md）
# PJ 未決の間の暫定（アプリ無し。sb-base から直接。:3000 待ちをスキップ）
TPL_VMID=9100 APP_WAIT_TRIES=0 sandbox/proxmox/run.sh 40-pool.sh generic 3
```
やること（1台ごと）: `qm clone 9110 92NN --name sb-{pj}-NN --full 0` → `--ipconfig0 ip=10.77.1.NN/16,gw=10.77.0.1` → 起動 → ssh 待ち → `sandbox-app` が :3000 で応答するまで待つ → `qm snapshot 92NN clean --vmstate 1`。`sb-gw` の `/etc/sandbox/hosts` に `sb-{pj}-NN` を常設登録。

### 5b. CLI 配置
```bash
sandbox/bin/install.sh   # ~/.local/bin/sandbox に実体コピー（シンボリックリンクだと launchd の gh-refresh が TCC で読めない）。CLI を更新したら再実行
sandbox ls
```

完了条件:
```bash
ssh "$PVE_HOST" 'qm list | grep sb-; for i in 9201 9202 9203; do qm listsnapshot $i; done'
sandbox take {pj} 001 && sandbox ssh 001 'cat /run/sandbox/env | cut -d= -f1; hostname' && sandbox url 001
curl -s -o /dev/null -w "%{http_code}\n" "$(sandbox url 001)"
sandbox ssh 001 'touch /home/dev/app/DIRTY' && sandbox reset 001 && sandbox ssh 001 'test ! -e /home/dev/app/DIRTY && echo clean'
sandbox release 001 && sandbox ls
```
- [ ] 3台とも running、スナップショット `clean` あり
- [ ] `take` で `/run/sandbox/env` に `TASK_ID` `PJ` `CLAUDE_CODE_OAUTH_TOKEN` のキーがある
- [ ] `url` が 200/302
- [ ] `reset` で DIRTY ファイルが消える
- [ ] `release` 後に `ls` の貸出が空

---

## Step 5c. 通信制限（ADR-0010） 🤖
```bash
LENT="$(jq -r '.[].vmid' ~/.config/sandbox/state.json | tr '\n' ' ')" sandbox/proxmox/run.sh 50-firewall.sh   # 貸出中は飛ばす。release 後に再実行
```
やること: datacenter firewall 有効化（ホスト側は ACCEPT）→ group `sandbox` → sb-* 全 VM とテンプレートに net0 firewall=1 と .fw → プールの clean を取り直し → sb-gw の FORWARD DROP。
**貸出中の VM を `LENT` で必ず除外する**。含めると、その VM の作業状態が `clean` に写り込む（実機で踏んだ。汚れた VM は破棄してテンプレートから作り直すしかない）。
完了条件（汎用 VM を take して中から）: LAN（Proxmox ホストの属するネットワーク）・ホスト 10.77.0.1・隣 VM・tailnet（CGNAT 範囲）へ **届かない**、GitHub と DNS へ **届く**。Mac から `sandbox ssh` と `url` が **届く**。

## Step 6. 1周まわす（最小 workflow で契約を検証） 🤖 + 🧑

目的: sandbox の5操作が workflow から見て足りているかを体感し、足りないものを `README.md` の未決と ADR に落とす。

1. cmux の team pane に surface を1つ立て、`sandbox take {pj} 002 && sandbox ssh 002` で VM に入る
2. VM 内で `cd ~/app && claude` を起動（`CLAUDE_CODE_OAUTH_TOKEN` は profile で読み込まれている）。小さな修正を1つ依頼する
3. VM 内で PJ の `gates.sh` を **コードとして**走らせ、失敗なら結果を Claude に戻す（これが最小ループ）
4. Mac のブラウザで `sandbox url 002` を開き画面を確認する
5. `git push` でブランチを出し、`sandbox release 002`

観察して記録すること（`$AIFACTORY_WORKSPACE/docs/STATUS.md` の「1周の所見」へ）:
- take から Claude 起動までの秒数
- 足りなかった操作（例: スクショを Mac に持ってくる、ログを取り出す）
- 巻き戻しで消えて困ったもの、残って困ったもの

完了条件:
- [ ] 上記 1〜5 が止まらずに通る
- [ ] 所見が `$AIFACTORY_WORKSPACE/docs/STATUS.md` に書かれ、必要なら `README.md` の契約が更新されている
