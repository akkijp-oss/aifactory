#!/usr/bin/env bash
# _tenant.sh: テナント（貸出先の組織 = 1 つの独立した sandbox 環境）ごとの名前・番号・アドレスの導出。ADR-0017。
#   proxmox/*.sh の先頭で読み込む（run.sh は stdin でスクリプトを送るので、このファイルを前に連結して送る。
#   ホスト上で直接叩くときは各スクリプトが同じディレクトリのこのファイルを source する）。
#
# 命名規則（2026-09-07 メンテナ決定）: **すべての名前に sb と <t> を含める**。<t> = テナントの slug [a-z0-9]{1,6}
#   （Proxmox の SDN 名が英数字 8 文字以内なので 6 文字に絞る）。最初の環境も特別扱いしない（slug は main）。
#
# 入力（環境変数。未設定なら既定）:
#   SB_TENANT      テナントの slug。既定 main
#   SB_NET         /16 のプレフィックス。既定 10.77。テナントごとに違う値を必ず与える（重なると隔離が壊れる）
#   SB_VMID_BASE   VMID 帯の起点（1000 刻み）。既定 9000。テナントごとに別の帯にする
#   SB_ZONE / SB_VNET / SB_PREFIX / SB_FW_GROUP / SB_POOL / SB_PVE_USER / SB_DOMAIN は導出値を個別に上書きできる
# 導出:
#   SB_DOMAIN      DNS ドメイン                <t>.sb.internal     （gw. / ctl. / task-<id>. / <pj>-NN. を前に付ける）
#   SB_ZONE        SDN zone                    sb<t>               ※英数字 8 文字以内
#   SB_VNET        SDN vnet（= ブリッジ名）     vn<t>               ※同上
#   SB_PREFIX      VM / CT 名の接頭辞           sb-<t>              → sb-<t>-gw / -ctl / -base / -tpl-<pj> / -<pj>-NN
#   SB_FW_GROUP    firewall セキュリティグループ sb-<t>（VM 用）、sb-<t>-ctl（制御系用）
#   SB_POOL        Proxmox のリソースプール      sb-<t>
#   SB_PVE_USER    制御系が使う Proxmox ユーザー  sb-<t>@pve（トークン id は ctl）
#   SB_GW_CT       ゲートウェイ LXC の VMID       SB_VMID_BASE + 0
#   SB_CTL_CT      制御系 LXC の VMID             SB_VMID_BASE + 1
#   SB_BASE_VMID   ベーステンプレートの VMID      SB_VMID_BASE + 100
#   SB_TPL_BASE    PJ テンプレートの起点          SB_VMID_BASE + 110（TPL_VMID で個別指定。IP は SB_NET.0.(VMID - SB_VMID_BASE)）
#   SB_POOL_BASE   プール VM の起点               SB_VMID_BASE + 200（IP は SB_NET.1.(VMID - SB_POOL_BASE)）
#   SB_HOST_IP     ホスト（SDN gateway）  SB_NET.0.1 / SB_GW_IP  SB_NET.0.2 / SB_CTL_IP  SB_NET.0.3
SB_TENANT="${SB_TENANT:-main}"
[[ "$SB_TENANT" =~ ^[a-z0-9]{1,6}$ ]] || { echo "[error] SB_TENANT は [a-z0-9]{1,6}（SDN の名前 sb<t> / vn<t> が 8 文字以内のため）: $SB_TENANT" >&2; exit 1; }
SB_NET="${SB_NET:-10.77}"
SB_VMID_BASE="${SB_VMID_BASE:-9000}"
SB_ZONE="${SB_ZONE:-sb$SB_TENANT}"; SB_VNET="${SB_VNET:-vn$SB_TENANT}"; SB_PREFIX="${SB_PREFIX:-sb-$SB_TENANT}"
SB_FW_GROUP="${SB_FW_GROUP:-sb-$SB_TENANT}"; SB_POOL="${SB_POOL:-sb-$SB_TENANT}"; SB_DOMAIN="${SB_DOMAIN:-$SB_TENANT.sb.internal}"
SB_PVE_USER="${SB_PVE_USER:-${SB_POOL}@pve}"
SB_GW_CT="${SB_GW_CT:-$((SB_VMID_BASE + 0))}"
SB_CTL_CT="${SB_CTL_CT:-$((SB_VMID_BASE + 1))}"
SB_BASE_VMID="${SB_BASE_VMID:-$((SB_VMID_BASE + 100))}"
SB_TPL_BASE="${SB_TPL_BASE:-$((SB_VMID_BASE + 110))}"
SB_POOL_BASE="${SB_POOL_BASE:-$((SB_VMID_BASE + 200))}"
SB_HOST_IP="${SB_NET}.0.1"; SB_GW_IP="${SB_NET}.0.2"; SB_CTL_IP="${SB_NET}.0.3"
SB_TENANT_LOADED=1
# fw_vm <vmid> [group]: NIC 単位の firewall（ADR-0010）。net0 に firewall=1 と /etc/pve/firewall/<vmid>.fw に GROUP を書く。
#   net0 を変えたときだけ FW_CHANGED=1（clean スナップショットの取り直しが要るかの判定に使う）
fw_vm() {
  local id=$1 group=${2:-$SB_FW_GROUP} net0
  FW_CHANGED=0
  printf '[OPTIONS]\nenable: 1\npolicy_in: DROP\npolicy_out: ACCEPT\n\n[RULES]\nGROUP %s\n' "$group" > "/etc/pve/firewall/$id.fw"
  net0="$(qm config "$id" 2>/dev/null | sed -n 's/^net0: //p')"
  [[ -z "$net0" || "$net0" == *firewall=1* ]] || { qm set "$id" --net0 "${net0},firewall=1" >/dev/null; FW_CHANGED=1; }
}
# pool_add <vmid>: リソースプールに入れる（既に入っていれば何もしない）
pool_add() {
  pvesh get "/pools/$SB_POOL" --output-format json 2>/dev/null | grep -q "\"vmid\":$1[,}]" && return 0
  pvesh set "/pools/$SB_POOL" --vms "$1" >/dev/null 2>&1 || echo "[warn] pool $SB_POOL に $1 を入れられない（05-tenant.sh 未実行?）" >&2
}
# fw_ct <ctid> <group>: LXC の NIC 単位 firewall（制御系 LXC 用）
fw_ct() {
  local id=$1 group=$2 net0
  printf '[OPTIONS]\nenable: 1\npolicy_in: DROP\npolicy_out: ACCEPT\n\n[RULES]\nGROUP %s\n' "$group" > "/etc/pve/firewall/$id.fw"
  net0="$(pct config "$id" 2>/dev/null | sed -n 's/^net0: //p')"
  [[ -z "$net0" || "$net0" == *firewall=1* ]] || pct set "$id" --net0 "${net0},firewall=1" >/dev/null
}
# pool_members: このテナントのプールにある VM / CT を "vmid type name" で出す（pool が無ければ名前の接頭辞で代用）
pool_members() {
  if pvesh get "/pools/$SB_POOL" --output-format json >/tmp/.sb-pool.json 2>/dev/null; then
    python3 -c 'import json,sys; [print(m["vmid"], m["type"], m.get("name","")) for m in json.load(open(sys.argv[1]))["members"] if m["type"] in ("qemu","lxc")]' /tmp/.sb-pool.json
  else
    qm list 2>/dev/null | awk -v p="^$SB_PREFIX-" '$2 ~ p {print $1, "qemu", $2}'
    pct list 2>/dev/null | awk -v p="^$SB_PREFIX-" 'NR>1 && $NF ~ p {print $1, "lxc", $NF}'
  fi
}
sb_tenant_summary() {
  echo "[tenant] $SB_TENANT: zone=$SB_ZONE vnet=$SB_VNET net=$SB_NET.0.0/16 prefix=$SB_PREFIX group=$SB_FW_GROUP pool=$SB_POOL domain=$SB_DOMAIN vmid=$SB_VMID_BASE.. (gw $SB_GW_CT ctl $SB_CTL_CT base $SB_BASE_VMID pool $SB_POOL_BASE..)" >&2
}
