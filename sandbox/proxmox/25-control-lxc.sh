#!/usr/bin/env bash
# 25-control-lxc.sh: テナントの制御系 LXC `<prefix>-ctl`（VMID は SB_CTL_CT）を作る（BUILD.md Step 2d。ADR-0017）。冪等寄り。
#   eth0 = SB_VNET SB_CTL_IP/16（main: vnmain 10.77.0.3）。LAN には足を出さない。外向きはホストの SNAT、到達は gw の subnet router 経由
#   中身: aifactory の checkout + workspace + sandbox CLI（Proxmox API モード）+ Web コンソール（systemd）+ ドキュメントサイト（コンソールが /docs/ で配信）
#         + GitHub App トークン更新の timer + runner が使う python3 / gh / claude / ssh
#   これで Mac 側には何も要らない: ブラウザ（http://ctl.<domain>:8765/）と ssh（aifactory@ctl.<domain>）だけ
#   Proxmox API トークン（05-tenant.sh のユーザー）はここで発行して LXC の ~/.config/sandbox/env に直接書く（画面に出さない。再実行で作り直す）
#   env: AIFACTORY_REPO_URL（既定 https://github.com/akkijp-oss/aifactory.git）、AIFACTORY_REF（既定 main）、VM_MEMORY（既定 2048）、VM_CORES（既定 2）
#        AIFACTORY_LOCAL_TREE=1（run.sh が手元の作業ツリーを /tmp/aifactory-tree.tgz に置く。checkout の上に展開して未 push の変更で動かす）
set -euo pipefail
if [[ -z "${SB_TENANT_LOADED:-}" ]]; then _d="$(dirname "${BASH_SOURCE[0]:-.}")"; [[ -f "$_d/_tenant.sh" ]] && source "$_d/_tenant.sh" || { echo "[error] _tenant.sh が無い（sandbox/proxmox/run.sh 経由で実行する）" >&2; exit 1; }; fi
VMID="$SB_CTL_CT"
NAME="$SB_PREFIX-ctl"
IP="$SB_CTL_IP"
MEM="${VM_MEMORY:-2048}"; CORES="${VM_CORES:-2}"
REPO_URL="${AIFACTORY_REPO_URL:-https://github.com/akkijp-oss/aifactory.git}"
REF="${AIFACTORY_REF:-main}"
: "${SB_PUBKEY:?SB_PUBKEY (公開鍵) が必要。run.sh 経由で実行する}"
sb_tenant_summary
pvesh get "/pools/$SB_POOL" >/dev/null 2>&1 || { echo "[error] pool $SB_POOL が無い。先に 05-tenant.sh" >&2; exit 1; }

if pct status "$VMID" >/dev/null 2>&1; then
  echo "[skip] CT $VMID exists ($(pct status $VMID))"
else
  pveam update >/dev/null
  TPL="$(pveam available --section system | awk '/debian-12-standard/{print $2}' | sort -V | tail -1)"
  [[ -n "$TPL" ]] || { echo "[error] debian-12-standard template not found in pveam" >&2; exit 1; }
  pveam download local "$TPL" >/dev/null || true
  printf '%s\n' "$SB_PUBKEY" > /tmp/sb.pub
  pct create "$VMID" "local:vztmpl/$TPL" \
    --hostname "$NAME" --ostype debian \
    --memory "$MEM" --swap 512 --cores "$CORES" \
    --rootfs local-lvm:16 \
    --net0 "name=eth0,bridge=$SB_VNET,ip=${IP}/16,gw=$SB_HOST_IP" \
    --nameserver "$SB_GW_IP" --searchdomain "$SB_DOMAIN" \
    --unprivileged 1 --features nesting=1 \
    --onboot 1 --start 0 \
    --ssh-public-keys /tmp/sb.pub \
    --description "aifactory control plane ($SB_TENANT): console :8765 (+/docs/), runner, kanban, workspace. Proxmox API token scoped to pool $SB_POOL"
  echo "[ok] CT $VMID created"
fi
pool_add "$VMID"
fw_ct "$VMID" "${SB_FW_GROUP}-ctl"
pct status "$VMID" | grep -q running || pct start "$VMID"
sleep 5

# --- Proxmox API トークン（このテナントのプールだけに効く）。毎回作り直して LXC に直接書く
TOKEN_ID=ctl
pveum user token remove "$SB_PVE_USER" "$TOKEN_ID" >/dev/null 2>&1 || true
tok_json="$(pveum user token add "$SB_PVE_USER" "$TOKEN_ID" --privsep 0 --comment "aifactory $SB_TENANT control plane ($NAME)" --output-format json)"
PVE_API_TOKEN="$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["full-tokenid"] + "=" + d["value"])' <<< "$tok_json")"
PVE_CA="$(cat /etc/pve/pve-root-ca.pem)"
NODE="$(hostname)"   # API の証明書の SAN はノード名（と LAN の IP）。URL はノード名、名前解決は SDN 側の IP に向ける（PVE_API_RESOLVE）

# --- 手元の作業ツリー（run.sh AIFACTORY_LOCAL_TREE=1）を LXC に渡す
if [[ "${AIFACTORY_LOCAL_TREE:-}" == 1 && -f /tmp/aifactory-tree.tgz ]]; then
  pct push "$VMID" /tmp/aifactory-tree.tgz /tmp/aifactory-tree.tgz >/dev/null && rm -f /tmp/aifactory-tree.tgz
  echo "[ok] local tree → CT $VMID:/tmp/aifactory-tree.tgz"
fi

# --- 中身（root で）。値は env で渡す
pct exec "$VMID" -- env IP="$IP" SB_NET="$SB_NET" SB_GW_IP="$SB_GW_IP" SB_HOST_IP="$SB_HOST_IP" SB_DOMAIN="$SB_DOMAIN" SB_PREFIX="$SB_PREFIX" SB_TENANT="$SB_TENANT" \
  SB_POOL="$SB_POOL" SB_POOL_BASE="$SB_POOL_BASE" PVE_API_TOKEN="$PVE_API_TOKEN" PVE_CA="$PVE_CA" NODE="$NODE" REPO_URL="$REPO_URL" REF="$REF" SB_PUBKEY="$SB_PUBKEY" bash -s <<'INNER'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8
U=aifactory; H=/home/$U
echo "== packages"
apt-get update -qq
apt-get install -y -qq curl ca-certificates gnupg git jq sudo openssh-client netcat-openbsd python3 python3-yaml python3-jsonschema python3-venv python3-pip locales unzip >/dev/null
sed -i 's/^# *ja_JP.UTF-8/ja_JP.UTF-8/; s/^# *en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen; locale-gen >/dev/null
if ! command -v gh >/dev/null; then
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list
  apt-get update -qq && apt-get install -y -qq gh >/dev/null
fi
echo "== user $U"
id "$U" >/dev/null 2>&1 || useradd -m -s /bin/bash "$U"
echo "$U ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/$U; chmod 440 /etc/sudoers.d/$U
install -d -m 700 -o $U -g $U "$H/.ssh" "$H/.ssh/conf.d" "$H/.ssh/conf.d/aifactory" "$H/.config" "$H/.config/sandbox" "$H/.config/sandbox/pj" "$H/.config/aifactory"
touch "$H/.ssh/authorized_keys"; chmod 600 "$H/.ssh/authorized_keys"; chown $U:$U "$H/.ssh/authorized_keys"
while IFS= read -r k; do [[ -n "$k" ]] && { grep -qxF "$k" "$H/.ssh/authorized_keys" || echo "$k" >> "$H/.ssh/authorized_keys"; }; done <<< "$SB_PUBKEY"
[[ -f "$H/.ssh/conf.d/aifactory/sb_ed25519" ]] || sudo -u $U ssh-keygen -q -t ed25519 -N "" -C "aifactory-ctl-$SB_TENANT" -f "$H/.ssh/conf.d/aifactory/sb_ed25519"
grep -q "conf.d/aifactory" "$H/.ssh/config" 2>/dev/null || echo "Include ~/.ssh/conf.d/aifactory/config" >> "$H/.ssh/config"
chown $U:$U "$H/.ssh/config"; chmod 600 "$H/.ssh/config"

echo "== repo $REPO_URL ($REF)"
if [[ -d "$H/aifactory/.git" ]]; then sudo -u $U git -C "$H/aifactory" fetch -q origin && sudo -u $U git -C "$H/aifactory" checkout -q "$REF" && sudo -u $U git -C "$H/aifactory" pull -q --ff-only || true
else sudo -u $U git clone -q --branch "$REF" "$REPO_URL" "$H/aifactory"; fi
R="$H/aifactory"
if [[ -f /tmp/aifactory-tree.tgz ]]; then
  echo "== local tree → $R (未 push の変更を checkout の上に展開)"
  sudo -u $U tar -xzf /tmp/aifactory-tree.tgz -C "$R" && rm -f /tmp/aifactory-tree.tgz
  sudo -u $U git -C "$R" status --short | wc -l | xargs -I{} echo "   {} files differ from $(sudo -u $U git -C "$R" rev-parse --short HEAD)"
fi

echo "== workspace"
install -d -o $U -g $U "$H/workspace" "$H/workspace/projects" "$H/workspace/kanban" "$H/workspace/runs" "$H/workspace/logs" "$H/workspace/docs"
echo "$H/workspace" > "$H/.config/aifactory/workspace"; chown $U:$U "$H/.config/aifactory/workspace"

echo "== ssh config / sandbox env (API mode)"
sed -e "s#10\.77\.#$SB_NET.#g" "$R/sandbox/templates/ssh_config.example" > "$H/.ssh/conf.d/aifactory/config"
chown $U:$U "$H/.ssh/conf.d/aifactory/config"; chmod 600 "$H/.ssh/conf.d/aifactory/config"
printf '%s\n' "$PVE_CA" > "$H/.config/sandbox/pve-ca.pem"; chown $U:$U "$H/.config/sandbox/pve-ca.pem"
ENVF="$H/.config/sandbox/env"
if [[ ! -f "$ENVF" ]]; then
  cat > "$ENVF" <<EOT
# ~/.config/sandbox/env: テナント $SB_TENANT の制御系（25-control-lxc.sh が生成）。sandbox CLI は Proxmox API モードで動く（ホストの root は持たない）
SB_TENANT=$SB_TENANT
SB_PREFIX=$SB_PREFIX
PVE_HOST=
PVE_API_URL=https://$NODE:8006
PVE_API_RESOLVE=$NODE:8006:$SB_HOST_IP
PVE_API_CA=$H/.config/sandbox/pve-ca.pem
SB_POOL=$SB_POOL
GW_SSH=root@$SB_GW_IP
SB_KEY=$H/.ssh/conf.d/aifactory/sb_ed25519
SB_DOMAIN=$SB_DOMAIN
APP_PORT=3000
SB_JUMP=
SB_POOL_NET=$SB_NET.1
SB_POOL_BASE=$SB_POOL_BASE
SB_NET=$SB_NET
# Claude の鍵はここには置かない（鍵プール keys.json。sandbox keys add / console の「鍵」画面。ADR-0060）
# GH_TOKEN は GitHub App（sandbox gh-app）が無いときだけ（sandbox token set global gh）
GH_TOKEN=
EOT
fi
# トークンと API の宛先は毎回書き直す（トークンは発行し直しているので）
upsert() { grep -q "^$1=" "$ENVF" && sed -i "s#^$1=.*#$1=$2#" "$ENVF" || echo "$1=$2" >> "$ENVF"; }
upsert PVE_API_TOKEN "$PVE_API_TOKEN"
upsert PVE_API_URL "https://$NODE:8006"
upsert PVE_API_RESOLVE "$NODE:8006:$SB_HOST_IP"
# 名前の導出値も毎回揃える（命名規則の変更・テナント改名に追随。秘密ではない）
upsert SB_TENANT "$SB_TENANT"; upsert SB_PREFIX "$SB_PREFIX"; upsert SB_POOL "$SB_POOL"; upsert SB_DOMAIN "$SB_DOMAIN"
upsert GW_SSH "root@$SB_GW_IP"; upsert SB_NET "$SB_NET"; upsert SB_POOL_NET "$SB_NET.1"; upsert SB_POOL_BASE "$SB_POOL_BASE"
chown $U:$U "$ENVF"; chmod 600 "$ENVF"
[[ -f "$H/.config/sandbox/state.json" ]] || { echo '{}' > "$H/.config/sandbox/state.json"; chown $U:$U "$H/.config/sandbox/state.json"; }

echo "== control-plane env (console token, LLM / GitHub for intake and runner)"
CTLENV="$H/.config/aifactory/ctl.env"
if [[ ! -f "$CTLENV" ]]; then
  cat > "$CTLENV" <<EOT
# ~/.config/aifactory/ctl.env: 制御系のプロセス（console / gh-refresh timer）に渡す環境。secrets はここ（600）。systemd の EnvironmentFile
# コンソールの合言葉（tailnet の中でも念のため）。ブラウザは http://ctl.$SB_DOMAIN:8765/?token=<この値> で 1 回入ると cookie に残る
CONSOLE_TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
CONSOLE_HOST=$IP
CONSOLE_PORT=8765
# intake（自由文 → チケット）が claude -p を制御系で 1 回呼ぶ。VM 内の agent 用の鍵（鍵プール）とは別に、ここにも長期トークンを置く（claude setup-token → sandbox token rotate claude）
CLAUDE_CODE_OAUTH_TOKEN=
# runner が制御系で gh pr view / gh を使うときのトークン。GitHub App（sandbox gh-app）が設定済みなら空でよい（runner が sandbox gh-app token <pj> で払い出す。ADR-0030）。App が無いときだけ fine-grained PAT を入れる
GH_TOKEN=
EOT
  chown $U:$U "$CTLENV"; chmod 600 "$CTLENV"
fi

echo "== claude code (native installer, user $U)"
sudo -u $U -H bash -c 'command -v ~/.local/bin/claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash' >/dev/null 2>&1 || echo "[warn] claude のインストールに失敗（intake だけが使う。後で aifactory ユーザーで curl -fsSL https://claude.ai/install.sh | bash）"

echo "== sandbox CLI + gh-refresh timer + console (systemd)"
sudo -u $U -H bash -c "$R/sandbox/bin/install.sh"
sudo -u $U -H bash -c "$R/sandbox/bin/install.sh --systemd"
sudo -u $U -H bash -c "cd $R/website && python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt && .venv/bin/mkdocs build -q -f mkdocs.yml" || echo "[warn] docs のビルドに失敗（後で website/ で mkdocs build）"
sudo -u $U -H env CONSOLE_HOST="$IP" CONSOLE_PORT=8765 bash -c "$R/console/bin/install.sh --systemd"
# ssh の非ログインシェル（ssh ctl 'sandbox ls'）や runner の子プロセスからも見えるように、/usr/local/bin にも置く
ln -sf "$H/.local/bin/sandbox" /usr/local/bin/sandbox; ln -sf "$H/.local/bin/aifactory-console" /usr/local/bin/aifactory-console
echo "--- inner verify"
ip -br a show eth0
sudo -u $U -H bash -c 'cd ~/aifactory && python3 lib/aifactory_paths.py | head -3'
systemctl is-active aifactory-console aifactory-gh-refresh.timer || true
curl -s -o /dev/null -w 'console: %{http_code}\n' "http://$IP:8765/api/overview" || true
echo "== ctl public key（run.sh が次回から VM / gw に入れる。既存の gw には 20-gateway-lxc.sh を再実行）"
cat "$H/.ssh/conf.d/aifactory/sb_ed25519.pub"
INNER

# gw の authorized_keys に制御系の鍵を足す（既にあれば何もしない。gw が無ければ 20 の実行時に run.sh が拾う）
ctl_key="$(pct exec "$VMID" -- cat /home/aifactory/.ssh/conf.d/aifactory/sb_ed25519.pub)"
if pct status "$SB_GW_CT" >/dev/null 2>&1; then
  pct exec "$SB_GW_CT" -- bash -c "grep -qxF '$ctl_key' /root/.ssh/authorized_keys || echo '$ctl_key' >> /root/.ssh/authorized_keys"
  pct exec "$SB_GW_CT" -- bash -c "grep -q ' ctl$' /etc/sandbox/hosts || echo '$IP $NAME console docs ctl' >> /etc/sandbox/hosts; systemctl reload dnsmasq" 2>/dev/null || true
  echo "[ok] gw: 制御系の鍵と DNS 名 ctl.$SB_DOMAIN を登録"
fi
echo
echo "[ok] $NAME ($VMID, $IP) 制御系の入口（tailnet から。gw の route 承認後）:"
echo "  console: http://ctl.$SB_DOMAIN:8765/?token=<ctl の ~/.config/aifactory/ctl.env の CONSOLE_TOKEN>   docs: http://ctl.$SB_DOMAIN:8765/docs/"
echo "  ssh:     ssh -i <鍵> aifactory@ctl.$SB_DOMAIN   （$IP）"
echo "[next] 貸出先が入れる secrets（ctl の中で）: sandbox keys add <名前> --fable --other（Claude の鍵。console の「鍵」画面でも可） / sandbox/bin/gh-app-setup / sandbox token rotate claude（intake 用。~/.config/aifactory/ctl.env の CLAUDE_CODE_OAUTH_TOKEN）"
echo "[next] base テンプレート以降は制御系の鍵込みで焼く: 30-base-template.sh create（既存テンプレートには鍵が無いので作り直す）"
