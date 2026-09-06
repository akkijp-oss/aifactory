#!/usr/bin/env bash
# 10-sdn.sh: Proxmox ホストに sandbox 専用 SDN を作る（BUILD.md Step 1）。冪等。
#   zone   sb     (simple, nodes=SB_NODE。既定はこのホストの hostname)
#   vnet   sbnet
#   subnet SB_NET.0.0/16  gateway SB_NET.0.1  snat on   （SB_NET 既定 10.77）
set -euo pipefail

SB_NET="${SB_NET:-10.77}"           # sandbox ネットワークの /16 プレフィックス
SB_NODE="${SB_NODE:-$(hostname)}"   # SDN zone を載せる Proxmox ノード名
NODE="$SB_NODE"
ZONE=sb
VNET=sbnet
CIDR="${SB_NET}.0.0/16"
GW="${SB_NET}.0.1"
SUBNET_ID="${ZONE}-${CIDR//\//-}"   # 例: sb-10.77.0.0-16

[[ "$(hostname)" == "$NODE" ]] || { echo "[error] run on $NODE (now: $(hostname))" >&2; exit 1; }

if pvesh get "/cluster/sdn/zones/$ZONE" >/dev/null 2>&1; then
  echo "[skip] zone $ZONE exists"
else
  pvesh create /cluster/sdn/zones --zone "$ZONE" --type simple --nodes "$NODE" --ipam pve
  echo "[ok] zone $ZONE"
fi

if pvesh get "/cluster/sdn/vnets/$VNET" >/dev/null 2>&1; then
  echo "[skip] vnet $VNET exists"
else
  pvesh create /cluster/sdn/vnets --vnet "$VNET" --zone "$ZONE"
  echo "[ok] vnet $VNET"
fi

if pvesh get "/cluster/sdn/vnets/$VNET/subnets/$SUBNET_ID" >/dev/null 2>&1; then
  echo "[skip] subnet $SUBNET_ID exists"
else
  pvesh create "/cluster/sdn/vnets/$VNET/subnets" --subnet "$CIDR" --type subnet --gateway "$GW" --snat 1
  echo "[ok] subnet $CIDR gw $GW snat"
fi

pvesh set /cluster/sdn
sleep 3
echo "--- verify"
ip -br a show "$VNET"
iptables -t nat -S | grep -F "$SB_NET" || echo "[warn] no MASQUERADE rule yet (再適用: pvesh set /cluster/sdn)"
