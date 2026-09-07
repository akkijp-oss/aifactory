#!/usr/bin/env bash
# 20-gateway-lxc.sh: ゲートウェイ LXC `<prefix>-gw`（VMID は SB_GW_CT）を作る（BUILD.md Step 2a）。冪等寄り。
#   eth0 = vmbr0 (LAN, DHCP)         … Tailscale の足。管理用（LAN 側の実値は STATUS に書く）
#   eth1 = SB_VNET SB_GW_IP/16       … sandbox 側。dnsmasq がここで応答（main: vnmain 10.77.0.2）
#   役割: Tailscale subnet router (SB_NET.0.0/16 を広告) + dnsmasq (*.SB_DOMAIN)
# 実行後、BUILD.md Step 2b（人間の操作）で `tailscale up` を行う。貸出先の組織は **自分の tailnet** で up する（ADR-0017）。
set -euo pipefail
if [[ -z "${SB_TENANT_LOADED:-}" ]]; then _d="$(dirname "${BASH_SOURCE[0]:-.}")"; [[ -f "$_d/_tenant.sh" ]] && source "$_d/_tenant.sh" || { echo "[error] _tenant.sh が無い（sandbox/proxmox/run.sh 経由で実行する）" >&2; exit 1; }; fi
VMID="$SB_GW_CT"
NAME="$SB_PREFIX-gw"
IP_SB="$SB_GW_IP"
: "${SB_PUBKEY:?SB_PUBKEY (公開鍵) が必要。run.sh 経由で実行する}"
sb_tenant_summary

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
    --memory 512 --swap 0 --cores 1 \
    --rootfs local-lvm:8 \
    --net0 "name=eth0,bridge=vmbr0,ip=dhcp" \
    --net1 "name=eth1,bridge=$SB_VNET,ip=${IP_SB}/16" \
    --nameserver 1.1.1.1 \
    --unprivileged 1 --features nesting=1 \
    --onboot 1 --start 0 \
    --ssh-public-keys /tmp/sb.pub \
    --description "aifactory sandbox gateway ($SB_TENANT): tailscale subnet router (${SB_NET}.0.0/16) + dnsmasq (*.$SB_DOMAIN)"

  # Tailscale (userspace でも動くが、subnet router は TUN が速い) のため /dev/net/tun を通す
  cat >> "/etc/pve/lxc/${VMID}.conf" <<'EOC'
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
EOC
  echo "[ok] CT $VMID created"
fi
pool_add "$VMID"
pct status "$VMID" | grep -q running || pct start "$VMID"
sleep 5
# 鍵の追加（既存 CT に制御系の鍵を足すとき。起動後に行う。新規作成時は --ssh-public-keys で入っている）
pct exec "$VMID" -- bash -c 'mkdir -p /root/.ssh; chmod 700 /root/.ssh; touch /root/.ssh/authorized_keys'
while IFS= read -r k; do [[ -n "$k" ]] && pct exec "$VMID" -- bash -c "grep -qxF '$k' /root/.ssh/authorized_keys || echo '$k' >> /root/.ssh/authorized_keys"; done <<< "$SB_PUBKEY"

# --- 中身のセットアップ（冪等）。ヒアドキュメントはクォート付きなので、ホスト側の値は env で渡す
pct exec "$VMID" -- env IP_SB="$IP_SB" SB_DOMAIN="$SB_DOMAIN" NAME="$NAME" CTL_IP="$SB_CTL_IP" CTL_NAME="$SB_PREFIX-ctl" bash -s <<'INNER'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8
apt-get update -qq
apt-get install -y -qq curl ca-certificates dnsmasq dnsutils iproute2 iptables >/dev/null

# IP forwarding（subnet router に必須）
cat > /etc/sysctl.d/99-sandbox-forward.conf <<EOS
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOS
# 非特権 LXC では kernel.* 等に触れず --system が非0で終わるので、必要なキーだけ個別に適用
sysctl -q -w net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1

# Tailscale
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh >/dev/null
fi
systemctl enable --now tailscaled >/dev/null

# dnsmasq: *.SB_DOMAIN を /etc/sandbox/hosts から返す。上流は 1.1.1.1 / 8.8.8.8
mkdir -p /etc/sandbox
touch /etc/sandbox/hosts
grep -q " gw$" /etc/sandbox/hosts || echo "$IP_SB gw $NAME" >> /etc/sandbox/hosts
grep -q " ctl$" /etc/sandbox/hosts || echo "$CTL_IP $CTL_NAME console docs ctl" >> /etc/sandbox/hosts
cat > /etc/dnsmasq.d/sandbox.conf <<EOS
# aifactory sandbox DNS ($SB_DOMAIN)
domain-needed
bogus-priv
no-resolv
server=1.1.1.1
server=8.8.8.8
local=/$SB_DOMAIN/
domain=$SB_DOMAIN
expand-hosts
no-hosts
addn-hosts=/etc/sandbox/hosts
interface=lo
interface=eth1
interface=tailscale0
bind-dynamic
cache-size=1000
EOS
# Debian 既定の /etc/dnsmasq.conf は全行コメントなので触らない。resolvconf 連携は切る
sed -i 's/^#\?IGNORE_RESOLVCONF=.*/IGNORE_RESOLVCONF=yes/' /etc/default/dnsmasq 2>/dev/null || true
systemctl enable dnsmasq >/dev/null
systemctl restart dnsmasq

# VM 発の転送を落とす（ADR-0010）: sandbox VM から tailnet / LAN へ gw 経由で出られないようにする。tailnet → VM（tailscale0 → eth1）は影響しない
cat > /etc/systemd/system/sandbox-fw.service <<'EOF2'
[Unit]
Description=aifactory sandbox gateway firewall (drop VM-originated NEW connections; replies pass)
After=network.target tailscaled.service
Wants=tailscaled.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c "iptables -C FORWARD -i eth1 -o tailscale0 -m conntrack --ctstate NEW -j DROP 2>/dev/null || iptables -I FORWARD 1 -i eth1 -o tailscale0 -m conntrack --ctstate NEW -j DROP"
ExecStart=/bin/sh -c "iptables -C FORWARD -i eth1 -o eth0 -m conntrack --ctstate NEW -j DROP 2>/dev/null || iptables -I FORWARD 1 -i eth1 -o eth0 -m conntrack --ctstate NEW -j DROP"
[Install]
WantedBy=multi-user.target
EOF2
systemctl daemon-reload; systemctl enable --now sandbox-fw >/dev/null
echo "--- inner verify"
ip -br a
systemctl is-active tailscaled dnsmasq
dig +short "gw.$SB_DOMAIN" @127.0.0.1
INNER

echo
echo "[next] BUILD.md Step 2b（人間の操作。貸出先の組織なら、その組織の Tailscale アカウントで）:"
echo "  pct exec $VMID -- tailscale up --advertise-routes=${SB_NET}.0.0/16 --accept-dns=false --hostname=$NAME"
echo "  → 表示された URL を承認 → 管理コンソールで route を Approve → ACL に ${SB_NET}.0.0/16 宛て許可 → split DNS: $SB_DOMAIN → $NAME の Tailscale IP"
