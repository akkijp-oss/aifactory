#!/usr/bin/env bash
# 05-tenant.sh: テナント（貸出先の組織）の器を Proxmox に作る（BUILD.md Step 0c。ADR-0017）。冪等。
#   05-tenant.sh            リソースプール・ロール・ユーザー・ACL を作る（API トークンは 25-control-lxc.sh が制御系 LXC に直接書く）
#   05-tenant.sh token      API トークン ctl を作り直して表示する（制御系 LXC を使わず手元の sandbox CLI から API で叩くとき。
#                           制御系 LXC があるなら 25-control-lxc.sh を再実行するほうが安全 = 画面に出ない）
#   05-tenant.sh adopt      既にある <prefix>-* の VM / CT をプールに入れる（この仕組みより前に作った環境の取り込み）
#   05-tenant.sh show       プールの中身と権限
# テナントの制御系（<prefix>-ctl の sandbox CLI）は、ここで作る API トークンで **このプールの VM だけ** を
# 見る・起動する・スナップショットに巻き戻す。ホストの root は渡さない。
set -euo pipefail
if [[ -z "${SB_TENANT_LOADED:-}" ]]; then _d="$(dirname "${BASH_SOURCE[0]:-.}")"; [[ -f "$_d/_tenant.sh" ]] && source "$_d/_tenant.sh" || { echo "[error] _tenant.sh が無い（sandbox/proxmox/run.sh 経由で実行する）" >&2; exit 1; }; fi
ROLE=AifactorySandbox
# 必要最小: 一覧と状態（VM.Audit / Pool.Audit）、起動停止（VM.PowerMgmt）、clean への巻き戻し（VM.Snapshot.Rollback。VM.Snapshot は listsnapshot に要る）
PRIVS="VM.Audit VM.PowerMgmt VM.Snapshot VM.Snapshot.Rollback Pool.Audit"
TOKEN_ID=ctl
sb_tenant_summary

make_token() {
  pveum user token remove "$SB_PVE_USER" "$TOKEN_ID" >/dev/null 2>&1 || true
  local out; out="$(pveum user token add "$SB_PVE_USER" "$TOKEN_ID" --privsep 0 --comment "aifactory $SB_TENANT control plane" --output-format json)"
  local full secret
  full="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["full-tokenid"])' <<< "$out")"
  secret="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["value"])' <<< "$out")"
  echo
  echo "== API トークン（今しか表示しない。制御系 LXC の ~/.config/sandbox/env に写す。25-control-lxc.sh は次の 3 行を受け取る）"
  echo "PVE_API_URL=https://$SB_HOST_IP:8006"
  echo "PVE_API_TOKEN=$full=$secret"
  echo "SB_POOL=$SB_POOL"
  echo
}

case "${1:-create}" in
  create)
    if pvesh get "/pools/$SB_POOL" >/dev/null 2>&1; then echo "[skip] pool $SB_POOL exists"
    else pveum pool add "$SB_POOL" --comment "aifactory tenant $SB_TENANT ($SB_NET.0.0/16, vmid $SB_VMID_BASE..)"; echo "[ok] pool $SB_POOL"; fi
    if pveum role list --output-format json | grep -q "\"roleid\":\"$ROLE\""; then pveum role modify "$ROLE" --privs "$PRIVS"; echo "[ok] role $ROLE (privs 更新)"
    else pveum role add "$ROLE" --privs "$PRIVS"; echo "[ok] role $ROLE"; fi
    if pveum user list --output-format json | grep -q "\"userid\":\"$SB_PVE_USER\""; then echo "[skip] user $SB_PVE_USER exists"
    else pveum user add "$SB_PVE_USER" --comment "aifactory tenant $SB_TENANT control plane"; echo "[ok] user $SB_PVE_USER"; fi
    pveum acl modify "/pool/$SB_POOL" --users "$SB_PVE_USER" --roles "$ROLE"
    echo "[ok] acl /pool/$SB_POOL $SB_PVE_USER $ROLE"
    echo "[next] 25-control-lxc.sh が API トークン $SB_PVE_USER!$TOKEN_ID を発行して制御系 LXC に書く（手元から API で使うなら 05-tenant.sh token）"
    ;;
  token) make_token ;;
  adopt)
    for id in $(qm list | awk -v p="^$SB_PREFIX-" '$2 ~ p {print $1}'); do pool_add "$id" && echo "[ok] pool += $id ($(qm config $id | sed -n 's/^name: //p'))"; done
    for id in $(pct list | awk -v p="^$SB_PREFIX-" 'NR>1 && $NF ~ p {print $1}'); do pool_add "$id" && echo "[ok] pool += $id (ct)"; done
    ;;
  show)
    pvesh get "/pools/$SB_POOL" --output-format json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print("pool:", d["poolid"], d.get("comment","")); [print(" ", m["vmid"], m["type"], m.get("name",""), m.get("status","")) for m in d["members"]]' || echo "pool $SB_POOL が無い"
    pveum acl list --output-format json | python3 -c 'import json,sys; [print("acl:", a["path"], a["ugid"], a["roleid"]) for a in json.load(sys.stdin) if a["path"].startswith("/pool/")]'
    ;;
  *) echo "usage: 05-tenant.sh [create|token|adopt|show]" >&2; exit 1 ;;
esac
