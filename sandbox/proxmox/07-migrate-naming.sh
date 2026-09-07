#!/usr/bin/env bash
# 07-migrate-naming.sh: 旧命名（zone sb / vnet sbnet / pool aifactory / VM 名 sb-<pj>-NN、または z<t> / v<t> / aifactory-<t>）で作った環境を、
#   命名規則（_tenant.sh: sb<t> / vn<t> / sb-<t> / sb-<t>-… / <t>.sb.internal）に移す一回限りの移行。ADR-0017 の 2026-09-07 追記。
#   07-migrate-naming.sh <old-zone> <old-vnet> <old-pool> <old-prefix> [old-user]
#     例: SB_TENANT=main  07-migrate-naming.sh sb sbnet aifactory sb aifactory@pve
#         SB_TENANT=demo  07-migrate-naming.sh zdemo vdemo aifactory-demo sb-demo aifactory-demo@pve
#   やること: 新 zone/vnet（subnet 無し）→ 旧プールの全 VM / CT の NIC を新 vnet に付け替え・改名・新プールへ → 旧 subnet/vnet/zone を消して新 vnet に subnet
#   → 旧 firewall group の節を消す → 旧ユーザー / プール削除 → gw の dnsmasq ドメインと hosts、Tailscale ホスト名 → プール VM の clean 取り直し（LENT は飛ばす）
#   前提: 貸出中の VM が無いこと（あるなら LENT に列挙。その VM は clean を取り直さないので、release 後に 45-pool-keys.sh 等で取り直す）
#   通信は subnet の付け替えの間（数十秒）だけ切れる。VM は再起動しない。
set -euo pipefail
if [[ -z "${SB_TENANT_LOADED:-}" ]]; then _d="$(dirname "${BASH_SOURCE[0]:-.}")"; [[ -f "$_d/_tenant.sh" ]] && source "$_d/_tenant.sh" || { echo "[error] _tenant.sh が無い（sandbox/proxmox/run.sh 経由で実行する）" >&2; exit 1; }; fi
OLD_ZONE="${1:?old zone}"; OLD_VNET="${2:?old vnet}"; OLD_POOL="${3:?old pool}"; OLD_PREFIX="${4:?old prefix}"; OLD_USER="${5:-${OLD_POOL}@pve}"
LENT="${LENT:-}"
CIDR="${SB_NET}.0.0/16"; SUBNET_ID_OLD="${OLD_ZONE}-${CIDR//\//-}"; SUBNET_ID_NEW="${SB_ZONE}-${CIDR//\//-}"
NODE="${SB_NODE:-$(hostname)}"
sb_tenant_summary
echo "[migrate] $OLD_ZONE/$OLD_VNET/$OLD_POOL/$OLD_PREFIX-* → $SB_ZONE/$SB_VNET/$SB_POOL/$SB_PREFIX-*"
pvesh get "/pools/$OLD_POOL" --output-format json > /tmp/.sb-oldpool.json 2>/dev/null || { echo "[error] pool $OLD_POOL が無い" >&2; exit 1; }

# 新しい名前に写す: sb-gw → sb-<t>-gw、sb-<pj>-NN → sb-<t>-<pj>-NN（既に新接頭辞ならそのまま）
newname() { local n=$1; [[ "$n" == "$SB_PREFIX-"* ]] && { echo "$n"; return; }; echo "$SB_PREFIX-${n#$OLD_PREFIX-}"; }

echo "== 1. 新 zone / vnet（subnet はまだ）"
pvesh get "/cluster/sdn/zones/$SB_ZONE" >/dev/null 2>&1 || pvesh create /cluster/sdn/zones --zone "$SB_ZONE" --type simple --nodes "$NODE" --ipam pve
pvesh get "/cluster/sdn/vnets/$SB_VNET" >/dev/null 2>&1 || pvesh create /cluster/sdn/vnets --vnet "$SB_VNET" --zone "$SB_ZONE"
pvesh set /cluster/sdn; sleep 3
ip -br a show "$SB_VNET" >/dev/null && echo "[ok] bridge $SB_VNET"

echo "== 2. プールとユーザー"
pvesh get "/pools/$SB_POOL" >/dev/null 2>&1 || pveum pool add "$SB_POOL" --comment "aifactory tenant $SB_TENANT ($CIDR, vmid $SB_VMID_BASE..)"
pveum user list --output-format json | grep -q "\"userid\":\"$SB_PVE_USER\"" || pveum user add "$SB_PVE_USER" --comment "aifactory tenant $SB_TENANT control plane"
pveum acl modify "/pool/$SB_POOL" --users "$SB_PVE_USER" --roles AifactorySandbox
echo "[ok] pool $SB_POOL / user $SB_PVE_USER"

echo "== 3. VM / CT の付け替え・改名・プール移動"
python3 -c 'import json,sys; [print(m["vmid"], m["type"], m.get("name","")) for m in json.load(open("/tmp/.sb-oldpool.json"))["members"] if m["type"] in ("qemu","lxc")]' | while read -r id type name; do
  nn="$(newname "$name")"
  if [[ "$type" == qemu ]]; then
    net0="$(qm config "$id" | sed -n 's/^net0: //p')"
    [[ "$net0" == *"bridge=$OLD_VNET"* ]] && qm set "$id" --net0 "${net0/bridge=$OLD_VNET/bridge=$SB_VNET}" >/dev/null
    [[ "$name" == "$nn" ]] || qm set "$id" --name "$nn" >/dev/null
  else
    for k in net0 net1; do
      v="$(pct config "$id" | sed -n "s/^$k: //p")"; [[ -n "$v" && "$v" == *"bridge=$OLD_VNET"* ]] && pct set "$id" --$k "${v/bridge=$OLD_VNET/bridge=$SB_VNET}" >/dev/null
    done
    [[ "$name" == "$nn" ]] || pct set "$id" --hostname "$nn" >/dev/null
  fi
  pvesh set "/pools/$OLD_POOL" --vms "$id" --delete 1 >/dev/null 2>&1 || true
  pool_add "$id"
  echo "[ok] $id $name → $nn (bridge $SB_VNET, pool $SB_POOL)"
done

echo "== 4. subnet を旧 vnet から新 vnet へ（この間だけ通信が切れる）"
pvesh delete "/cluster/sdn/vnets/$OLD_VNET/subnets/$SUBNET_ID_OLD" 2>/dev/null || true
pvesh delete "/cluster/sdn/vnets/$OLD_VNET" 2>/dev/null || true
pvesh delete "/cluster/sdn/zones/$OLD_ZONE" 2>/dev/null || true
pvesh get "/cluster/sdn/vnets/$SB_VNET/subnets/$SUBNET_ID_NEW" >/dev/null 2>&1 || pvesh create "/cluster/sdn/vnets/$SB_VNET/subnets" --subnet "$CIDR" --type subnet --gateway "$SB_HOST_IP" --snat 1
pvesh set /cluster/sdn; sleep 3
ip -br a show "$SB_VNET"; iptables -t nat -S | grep -F "$SB_NET" | head -2
echo "[ok] subnet $CIDR on $SB_VNET"

echo "== 5. 旧 firewall group の節を消す（新しい節は 50-firewall.sh が書く）"
if [[ -f /etc/pve/firewall/cluster.fw ]]; then
  awk -v g1="[group $OLD_PREFIX]" -v g2="[group $OLD_PREFIX-ctl]" -v g3="[group sandbox]" -v g4="[group sandbox-ctl]" '
    /^\[/ { keep = 1; for (i = 1; i <= 4; i++) { g = (i==1?g1:(i==2?g2:(i==3?g3:g4))); if (index($0, g) == 1) keep = 0 } }
    keep { print }' /etc/pve/firewall/cluster.fw > /tmp/.cluster.fw && cat /tmp/.cluster.fw > /etc/pve/firewall/cluster.fw && pve-firewall compile >/dev/null && echo "[ok] cluster.fw"
fi

echo "== 6. 旧ユーザー / プール"
[[ "$OLD_USER" != "$SB_PVE_USER" ]] && { pveum user delete "$OLD_USER" 2>/dev/null && echo "[ok] user $OLD_USER 削除" || true; }
[[ "$OLD_POOL" != "$SB_POOL" ]] && { pveum pool delete "$OLD_POOL" 2>/dev/null && echo "[ok] pool $OLD_POOL 削除" || echo "[warn] pool $OLD_POOL を消せない（まだメンバーがある?）"; }

echo "== 7. gw: dnsmasq のドメイン・hosts・Tailscale ホスト名"
pct exec "$SB_GW_CT" -- env D="$SB_DOMAIN" OP="$OLD_PREFIX" NP="$SB_PREFIX" GWN="$SB_PREFIX-gw" bash -s <<'INNER'
set -e
sed -i "s#^local=/.*/#local=/$D/#; s#^domain=.*#domain=$D#; s#^\# aifactory sandbox DNS.*#\# aifactory sandbox DNS ($D)#" /etc/dnsmasq.d/sandbox.conf
python3 - "$OP" "$NP" "$GWN" <<'PY' 2>/dev/null || perl -pi -e 's/ sb-gw( |$)/ gw $ENV{GWN}$1/; s/ (sb-)(?!$ENV{NP_TAIL})/ $ENV{NP}-/g' /etc/sandbox/hosts
import sys, re
op, np, gwn = sys.argv[1], sys.argv[2], sys.argv[3]
out = []
for line in open('/etc/sandbox/hosts'):
    parts = line.split()
    if not parts: continue
    ip, names = parts[0], parts[1:]
    new = []
    for n in names:
        if n in ('sb-gw', 'gw') or n == op + '-gw': new += ['gw', gwn]
        elif n in ('ctl', 'console', 'docs'): new.append(n)
        elif n.startswith(np + '-'): new.append(n)
        elif n.startswith(op + '-'): new.append(np + '-' + n[len(op) + 1:])
        else: new.append(n)
    seen = []; [seen.append(x) for x in new if x not in seen]
    out.append(ip + ' ' + ' '.join(seen) + '\n')
open('/etc/sandbox/hosts', 'w').writelines(out)
PY
systemctl restart dnsmasq
tailscale set --hostname "$GWN" 2>/dev/null || true
echo "--- hosts"; cat /etc/sandbox/hosts | head -5; dig +short "gw.$D" @127.0.0.1
INNER

echo "== 8. プール VM の clean 取り直し（NIC のブリッジ名が config に入るため）"
while read -r id type name; do
  [[ "$type" == qemu ]] || continue
  qm config "$id" | grep -q '^template: 1' && continue
  [[ " $LENT " == *" $id "* ]] && { echo "[skip] $id $name (貸出中)"; continue; }
  qm listsnapshot "$id" | grep -q '^`-> clean\|clean ' || { echo "[warn] $id $name: clean 無し"; continue; }
  qm delsnapshot "$id" clean >/dev/null
  qm snapshot "$id" clean --vmstate 1 --description "aifactory: clean state for take/reset, renamed to $SB_PREFIX ($(date -Iseconds))" >/dev/null
  echo "[ok] $id $name: clean 取り直し"
done < <(pool_members)
echo "[done] 次: 25-control-lxc.sh（制御系の env を新しい名前に）→ 50-firewall.sh → Tailscale split DNS を $SB_DOMAIN に → 手元の env / ssh config"
