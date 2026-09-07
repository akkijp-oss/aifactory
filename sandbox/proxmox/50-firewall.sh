#!/usr/bin/env bash
# 50-firewall.sh: sandbox VM の通信を「インターネットと gw の DNS だけ」に絞る（ADR-0010）。テナントごとのグループ（ADR-0017）。冪等。
#   - datacenter firewall を有効化（policy_in/out は ACCEPT = ホスト側の挙動は変えない）
#   - セキュリティグループ sb-<t>（SB_FW_GROUP）: IN は gw(SB_GW_IP)・ホスト(SB_HOST_IP)・制御系(SB_CTL_IP)・tailnet からの 22/3000/5174 と ICMP だけ、
#     OUT は gw の 53 を許可した上で RFC1918 / tailnet(100.64/10) 宛てを DROP（= 他テナントの網も含めて全部）、残り（インターネット）は許可
#   - セキュリティグループ sb-<t>-ctl（制御系 LXC 用）: IN は gw / ホストからの 22, 8765（コンソール）。OUT は自テナントの網（VM・gw・ホストの API :8006）と
#     インターネットだけ。LAN・他テナント・tailnet 宛ては DROP
#   - このテナントのプールにある VM とテンプレート全部: net0 に firewall=1、/etc/pve/firewall/<vmid>.fw に GROUP
#   - プール VM は clean スナップショットを取り直す（net0 の firewall=1 はスナップショットの config に含まれるため。
#     貸出中（LENT に vmid を列挙）の VM は飛ばす）
#   - gw: VM 発の tailnet 宛て転送を落とす（systemd unit で永続化）
#   cluster.fw の中では **自分のグループの節だけ** 書き換える（他テナントの節は触らない）
#   使い方: sandbox/proxmox/run.sh 50-firewall.sh            （LENT="9204 9213" で貸出中を除外）
set -euo pipefail
if [[ -z "${SB_TENANT_LOADED:-}" ]]; then _d="$(dirname "${BASH_SOURCE[0]:-.}")"; [[ -f "$_d/_tenant.sh" ]] && source "$_d/_tenant.sh" || { echo "[error] _tenant.sh が無い（sandbox/proxmox/run.sh 経由で実行する）" >&2; exit 1; }; fi
LENT="${LENT:-}"
GW_CT="$SB_GW_CT"; GW_IP="$SB_GW_IP"; HOST_IP="$SB_HOST_IP"; CTL_IP="$SB_CTL_IP"
GROUP="$SB_FW_GROUP"; GROUP_CTL="${SB_FW_GROUP}-ctl"
CLUSTER_FW=/etc/pve/firewall/cluster.fw
WORKER_RULE=""
if [[ "${SB_WORKER_PORT:-0}" != 0 ]]; then
  [[ "$SB_WORKER_PORT" =~ ^[0-9]+$ ]] && (( SB_WORKER_PORT >= 1024 && SB_WORKER_PORT <= 65535 )) || { echo "[error] invalid SB_WORKER_PORT" >&2; exit 1; }
  WORKER_RULE="IN ACCEPT -source ${GW_IP} -p tcp -dport ${SB_WORKER_PORT} # authenticated pull workers via gateway SNAT"
fi
sb_tenant_summary

echo "== 1. datacenter firewall + group $GROUP / $GROUP_CTL"
mkdir -p /etc/pve/firewall
# 既存の cluster.fw から [OPTIONS] 以外の「自分のグループ」の節を抜き、他は残す（無ければ OPTIONS から作る）
tmp="$(mktemp)"
if [[ -f "$CLUSTER_FW" ]]; then
  awk -v g1="[group $GROUP]" -v g2="[group $GROUP_CTL]" '
    /^\[/ { keep = 1; if (index($0, g1) == 1 || index($0, g2) == 1) keep = 0 }
    keep { print }' "$CLUSTER_FW" > "$tmp"
  if ! grep -q '^\[OPTIONS\]' "$tmp"; then
    { printf '[OPTIONS]\nenable: 1\npolicy_in: ACCEPT\npolicy_out: ACCEPT\n\n'; cat "$tmp"; } > "$tmp.2" && mv "$tmp.2" "$tmp"
  fi
else
  printf '# aifactory sandbox (sandbox/proxmox/50-firewall.sh が生成)。ホスト側は ACCEPT のまま（VM 単位のルールだけ使う）\n[OPTIONS]\nenable: 1\npolicy_in: ACCEPT\npolicy_out: ACCEPT\n\n' > "$tmp"
fi
cat >> "$tmp" <<EOG

[group $GROUP] # aifactory $SB_TENANT: sandbox VM はインターネットと gw の DNS だけ。LAN / 他 VM / 他テナント / tailnet へは出られない
IN ACCEPT -source ${GW_IP} -p tcp -dport 22,3000,5174 # tailnet からの ssh / アプリ（gw が SNAT して中継）
IN ACCEPT -source ${CTL_IP} -p tcp -dport 22,3000,5174 # 制御系 LXC（runner の sandbox ssh / scp）
IN ACCEPT -source ${HOST_IP} -p tcp -dport 22,3000,5174 # Proxmox ホストから（40-pool.sh の起動待ち、ProxyJump）
IN ACCEPT -source 100.64.0.0/10 -p tcp -dport 22,3000,5174 # tailnet 直（gw の SNAT を切った場合の保険）
IN ACCEPT -source ${GW_IP} -p icmp
IN ACCEPT -source ${CTL_IP} -p icmp
IN ACCEPT -source ${HOST_IP} -p icmp
OUT ACCEPT -dest ${GW_IP} -p udp -dport 53 # DNS
OUT ACCEPT -dest ${GW_IP} -p tcp -dport 53
OUT DROP -dest ${SB_NET}.0.0/16 # 他の sandbox VM・ホスト・gw の他ポート・制御系
OUT DROP -dest 192.168.0.0/16 # LAN（Proxmox 各ノード・管理コンソール・他 VM）
OUT DROP -dest 10.0.0.0/8 # 他テナントの網も含む
OUT DROP -dest 172.16.0.0/12
OUT DROP -dest 100.64.0.0/10 # tailnet
OUT DROP -dest 169.254.0.0/16 # link-local / メタデータ
OUT ACCEPT # インターネット（GitHub・レジストリ・Anthropic）

[group $GROUP_CTL] # aifactory $SB_TENANT: 制御系 LXC。自テナントの網とインターネットだけ
$WORKER_RULE
IN ACCEPT -source ${GW_IP} -p tcp -dport 22,8765 # tailnet からの ssh / コンソール（gw が SNAT して中継）
IN ACCEPT -source 100.64.0.0/10 -p tcp -dport 22,8765
IN ACCEPT -source ${HOST_IP} -p tcp -dport 22
IN ACCEPT -source ${GW_IP} -p icmp
IN ACCEPT -source ${HOST_IP} -p icmp
OUT ACCEPT -dest ${SB_NET}.0.0/16 # VM（ssh / scp / :3000）・gw（DNS・hosts 更新）・ホストの Proxmox API :8006
OUT DROP -dest 192.168.0.0/16
OUT DROP -dest 10.0.0.0/8 # 他テナントの網
OUT DROP -dest 172.16.0.0/12
OUT DROP -dest 100.64.0.0/10
OUT DROP -dest 169.254.0.0/16
OUT ACCEPT # インターネット（GitHub・Anthropic・apt）
EOG
cat "$tmp" > "$CLUSTER_FW"; rm -f "$tmp"
pve-firewall compile >/dev/null && echo "[ok] cluster.fw (group $GROUP, $GROUP_CTL)"

echo "== 2. templates / pools（このテナントのプールにあるもの）"
while read -r id type name; do
  [[ "$type" == qemu ]] || continue
  if qm config "$id" | grep -q '^template: 1'; then fw_vm "$id" "$GROUP"; echo "[ok] $id $name (template)"; continue; fi
  if [[ " $LENT " == *" $id "* ]]; then echo "[skip] $id $name (貸出中。release 後にもう一度実行)"; continue; fi
  fw_vm "$id" "$GROUP"
  if ! qm listsnapshot "$id" | grep -q '^`-> clean\|clean '; then
    echo "[warn] $id $name: snapshot clean が無い"
  elif (( FW_CHANGED )); then
    # clean の config に firewall=1 が入るよう取り直す（net0 を変えたときだけ）。VM は clean 直後の状態（release 済み）である前提
    qm delsnapshot "$id" clean >/dev/null
    qm snapshot "$id" clean --vmstate 1 --description "aifactory: clean state for take/reset, firewall on ($(date -Iseconds))" >/dev/null
    echo "[ok] $id $name: firewall on, snapshot clean 取り直し"
  else
    echo "[ok] $id $name: firewall on（clean はそのまま）"
  fi
done < <(pool_members)

echo "== 3. 制御系 LXC"
if pct status "$SB_CTL_CT" >/dev/null 2>&1; then fw_ct "$SB_CTL_CT" "$GROUP_CTL"; echo "[ok] $SB_CTL_CT $SB_PREFIX-ctl → group $GROUP_CTL"; else echo "[skip] $SB_PREFIX-ctl ($SB_CTL_CT) は無い（25-control-lxc.sh）"; fi

echo "== 4. gw: VM 発の tailnet 宛て転送を落とす"
pct exec "$GW_CT" -- bash -c 'cat > /etc/systemd/system/sandbox-fw.service <<EOS
[Unit]
Description=aifactory sandbox gateway firewall (drop VM-originated NEW connections into tailnet/LAN; replies to tailnet-initiated sessions pass)
After=network.target tailscaled.service
Wants=tailscaled.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c "iptables -C FORWARD -i eth1 -o tailscale0 -m conntrack --ctstate NEW -j DROP 2>/dev/null || iptables -I FORWARD 1 -i eth1 -o tailscale0 -m conntrack --ctstate NEW -j DROP"
ExecStart=/bin/sh -c "iptables -C FORWARD -i eth1 -o eth0 -m conntrack --ctstate NEW -j DROP 2>/dev/null || iptables -I FORWARD 1 -i eth1 -o eth0 -m conntrack --ctstate NEW -j DROP"
[Install]
WantedBy=multi-user.target
EOS
systemctl daemon-reload && systemctl enable --now sandbox-fw >/dev/null && iptables -S FORWARD | head -4'
echo "[ok] gw forward rules"

echo "== 5. verify"
pve-firewall status
while read -r id type name; do
  if [[ "$type" == qemu ]]; then printf '%s %s net0=%s fw=%s\n' "$id" "$name" "$(qm config $id | grep -c 'firewall=1')" "$(grep -c "GROUP $GROUP" /etc/pve/firewall/$id.fw 2>/dev/null)"
  else printf '%s %s net0=%s fw=%s\n' "$id" "$name" "$(pct config $id | grep -c 'firewall=1')" "$(grep -c 'GROUP ' /etc/pve/firewall/$id.fw 2>/dev/null)"; fi
done < <(pool_members) | column -t
