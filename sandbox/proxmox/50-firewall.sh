#!/usr/bin/env bash
# 50-firewall.sh: sandbox VM の通信を「インターネットと sb-gw の DNS だけ」に絞る（ADR-0010）。冪等。
#   - datacenter firewall を有効化（policy_in/out は ACCEPT = ホスト側の挙動は変えない）
#   - セキュリティグループ sandbox: IN は sb-gw(SB_NET.0.2)・ホスト(SB_NET.0.1)・tailnet からの 22/3000/5174 と ICMP だけ、
#     OUT は sb-gw の 53 を許可した上で RFC1918 / SB_NET.0.0/16 / tailnet(100.64/10) 宛てを DROP、残り（インターネット）は許可
#     （SB_NET 既定 10.77）
#   - sb-* の VM とテンプレート全部: net0 に firewall=1、/etc/pve/firewall/<vmid>.fw に group sandbox
#   - プール VM は clean スナップショットを取り直す（net0 の firewall=1 はスナップショットの config に含まれるため。
#     貸出中（LENT に vmid を列挙）の VM は飛ばす）
#   - sb-gw: VM 発の tailnet 宛て転送を落とす（systemd unit で永続化）
#   使い方: sandbox/proxmox/run.sh 50-firewall.sh            （LENT="9204 9213" で貸出中を除外）
set -euo pipefail
LENT="${LENT:-}"
SB_NET="${SB_NET:-10.77}"       # sandbox ネットワークの /16 プレフィックス
SB_GW_CT="${SB_GW_CT:-9000}"    # ゲートウェイ LXC の VMID
GW_CT="$SB_GW_CT"
GW_IP="${SB_NET}.0.2"           # sb-gw の sandbox 側アドレス
HOST_IP="${SB_NET}.0.1"         # Proxmox ホスト（SDN gateway）のアドレス

echo "== 1. datacenter firewall + group sandbox"
mkdir -p /etc/pve/firewall
# ヒアドキュメントは変数展開あり（中に $ は無い）
cat > /etc/pve/firewall/cluster.fw <<EOF
# aifactory sandbox (sandbox/proxmox/50-firewall.sh が生成)。ホスト側は ACCEPT のまま（VM 単位のルールだけ使う）
[OPTIONS]
enable: 1
policy_in: ACCEPT
policy_out: ACCEPT

[group sandbox] # sandbox VM: インターネットと sb-gw の DNS だけ。LAN / 他 VM / tailnet へは出られない
IN ACCEPT -source ${GW_IP} -p tcp -dport 22,3000,5174 # Mac からの ssh / アプリ（sb-gw が SNAT して中継）
IN ACCEPT -source ${HOST_IP} -p tcp -dport 22,3000,5174 # Proxmox ホストから（40-pool.sh の起動待ち、ProxyJump）
IN ACCEPT -source 100.64.0.0/10 -p tcp -dport 22,3000,5174 # tailnet 直（sb-gw の SNAT を切った場合の保険）
IN ACCEPT -source ${GW_IP} -p icmp
IN ACCEPT -source ${HOST_IP} -p icmp
OUT ACCEPT -dest ${GW_IP} -p udp -dport 53 # DNS
OUT ACCEPT -dest ${GW_IP} -p tcp -dport 53
OUT DROP -dest ${SB_NET}.0.0/16 # 他の sandbox VM・ホスト・sb-gw の他ポート
OUT DROP -dest 192.168.0.0/16 # LAN（Mac・Proxmox 各ノード・管理コンソール・他 VM）
OUT DROP -dest 10.0.0.0/8
OUT DROP -dest 172.16.0.0/12
OUT DROP -dest 100.64.0.0/10 # tailnet
OUT DROP -dest 169.254.0.0/16 # link-local / メタデータ
OUT ACCEPT # インターネット（GitHub・レジストリ・Anthropic）
EOF
pve-firewall compile >/dev/null && echo "[ok] cluster.fw"

vm_fw() { # vmid
  local id=$1
  cat > "/etc/pve/firewall/$id.fw" <<'EOF'
[OPTIONS]
enable: 1
policy_in: DROP
policy_out: ACCEPT

[RULES]
GROUP sandbox
EOF
  local net0; net0="$(qm config "$id" | sed -n 's/^net0: //p')"
  if [[ "$net0" != *firewall=1* ]]; then
    qm set "$id" --net0 "${net0},firewall=1" >/dev/null
  fi
}

echo "== 2. templates"
for id in $(qm list | awk '$2 ~ /^sb-(base|tpl-)/ {print $1}'); do vm_fw "$id"; echo "[ok] $id $(qm config $id | sed -n 's/^name: //p')"; done

echo "== 3. pools (clean スナップショットを取り直す)"
for id in $(qm list | awk '$2 ~ /^sb-/ && $2 !~ /^sb-(base|tpl-)/ {print $1}'); do
  name="$(qm config $id | sed -n 's/^name: //p')"
  if [[ " $LENT " == *" $id "* ]]; then echo "[skip] $id $name (貸出中。release 後にもう一度実行)"; continue; fi
  vm_fw "$id"
  if qm listsnapshot "$id" | grep -q '^`-> clean'; then
    # clean の config に firewall=1 が入るよう取り直す。VM は clean 直後の状態（release 済み）である前提
    qm delsnapshot "$id" clean >/dev/null
    qm snapshot "$id" clean --vmstate 1 --description "aifactory: clean state for take/reset, firewall on ($(date -Iseconds))" >/dev/null
    echo "[ok] $id $name: firewall on, snapshot clean 取り直し"
  else
    echo "[warn] $id $name: snapshot clean が無い"
  fi
done

echo "== 4. sb-gw: VM 発の tailnet 宛て転送を落とす"
pct exec "$GW_CT" -- bash -c 'cat > /etc/systemd/system/sandbox-fw.service <<EOF
[Unit]
Description=aifactory sandbox gateway firewall (drop VM-originated NEW connections into tailnet/LAN; replies to Mac-initiated sessions pass)
After=network.target tailscaled.service
Wants=tailscaled.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c "iptables -C FORWARD -i eth1 -o tailscale0 -m conntrack --ctstate NEW -j DROP 2>/dev/null || iptables -I FORWARD 1 -i eth1 -o tailscale0 -m conntrack --ctstate NEW -j DROP"
ExecStart=/bin/sh -c "iptables -C FORWARD -i eth1 -o eth0 -m conntrack --ctstate NEW -j DROP 2>/dev/null || iptables -I FORWARD 1 -i eth1 -o eth0 -m conntrack --ctstate NEW -j DROP"
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable --now sandbox-fw >/dev/null && iptables -S FORWARD | head -4'
echo "[ok] sb-gw forward rules"

echo "== 5. verify"
pve-firewall status
for id in $(qm list | awk '$2 ~ /^sb-/ {print $1}'); do printf '%s %s net0=%s fw=%s\n' "$id" "$(qm config $id | sed -n 's/^name: //p')" "$(qm config $id | grep -c 'firewall=1')" "$(grep -c 'GROUP sandbox' /etc/pve/firewall/$id.fw 2>/dev/null)"; done | column -t
