#!/usr/bin/env bash
# 20-gateway-lxc.sh: ゲートウェイ LXC `sb-gw`（VMID は SB_GW_CT、既定 9000）を作る（BUILD.md Step 2a）。冪等寄り。
#   eth0 = vmbr0 (LAN, DHCP)      … Tailscale の足。管理用
#   eth1 = sbnet SB_NET.0.2/16    … sandbox 側。dnsmasq がここで応答（SB_NET 既定 10.77 → 10.77.0.2）
#   役割: Tailscale subnet router (SB_NET.0.0/16 を広告) + dnsmasq (*.sb.internal)
# 実行後、BUILD.md Step 2b（人間の操作）で `tailscale up` を行う。
set -euo pipefail

SB_NET="${SB_NET:-10.77}"       # sandbox ネットワークの /16 プレフィックス
SB_GW_CT="${SB_GW_CT:-9000}"    # ゲートウェイ LXC の VMID
VMID="$SB_GW_CT"
NAME=sb-gw
IP_SB="${SB_NET}.0.2"
: "${SB_PUBKEY:?SB_PUBKEY (Mac の公開鍵) が必要。run.sh 経由で実行する}"

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
    --net1 "name=eth1,bridge=sbnet,ip=${IP_SB}/16" \
    --nameserver 1.1.1.1 \
    --unprivileged 1 --features nesting=1 \
    --onboot 1 --start 0 \
    --ssh-public-keys /tmp/sb.pub \
    --description "aifactory sandbox gateway: tailscale subnet router (${SB_NET}.0.0/16) + dnsmasq (*.sb.internal)"

  # Tailscale (userspace でも動くが、subnet router は TUN が速い) のため /dev/net/tun を通す
  cat >> "/etc/pve/lxc/${VMID}.conf" <<'EOF'
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
EOF
  echo "[ok] CT $VMID created"
fi

pct status "$VMID" | grep -q running || pct start "$VMID"
sleep 5

# --- 中身のセットアップ（冪等）。ヒアドキュメントはクォート付きなので、ホスト側の値は env で渡す
pct exec "$VMID" -- env IP_SB="$IP_SB" bash -s <<'INNER'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8
apt-get update -qq
apt-get install -y -qq curl ca-certificates dnsmasq dnsutils iproute2 iptables >/dev/null

# IP forwarding（subnet router に必須）
cat > /etc/sysctl.d/99-sandbox-forward.conf <<EOF
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF
# 非特権 LXC では kernel.* 等に触れず --system が非0で終わるので、必要なキーだけ個別に適用
sysctl -q -w net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1

# Tailscale
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh >/dev/null
fi
systemctl enable --now tailscaled >/dev/null

# dnsmasq: *.sb.internal を /etc/sandbox/hosts から返す。上流は 1.1.1.1 / 8.8.8.8
mkdir -p /etc/sandbox
touch /etc/sandbox/hosts
grep -q "sb-gw" /etc/sandbox/hosts || echo "$IP_SB sb-gw" >> /etc/sandbox/hosts
cat > /etc/dnsmasq.d/sandbox.conf <<'EOF'
# aifactory sandbox DNS
domain-needed
bogus-priv
no-resolv
server=1.1.1.1
server=8.8.8.8
local=/sb.internal/
domain=sb.internal
expand-hosts
no-hosts
addn-hosts=/etc/sandbox/hosts
interface=lo
interface=eth1
interface=tailscale0
bind-dynamic
cache-size=1000
EOF
# Debian 既定の /etc/dnsmasq.conf は全行コメントなので触らない。resolvconf 連携は切る
sed -i 's/^#\?IGNORE_RESOLVCONF=.*/IGNORE_RESOLVCONF=yes/' /etc/default/dnsmasq 2>/dev/null || true
systemctl enable dnsmasq >/dev/null
systemctl restart dnsmasq

# VM 発の転送を落とす（ADR-0010）: sandbox VM から tailnet / LAN へ sb-gw 経由で出られないようにする。Mac → VM（tailscale0 → eth1）は影響しない
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
dig +short sb-gw.sb.internal @127.0.0.1
INNER

echo
echo "[next] BUILD.md Step 2b（人間の操作）:"
echo "  pct exec $VMID -- tailscale up --advertise-routes=${SB_NET}.0.0/16 --accept-dns=false --hostname=$NAME"
echo "  → 表示された URL を承認 → 管理コンソールで route を Approve → split DNS: sb.internal → sb-gw の Tailscale IP"
