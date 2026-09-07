#!/usr/bin/env bash
# 32-pj-template.sh: PJ テンプレート `<prefix>-tpl-{pj}`（VMID は TPL_VMID。SB_TPL_BASE から PJ ごとに割る。main: 911x）を作る（BUILD.md Step 4）。
#   create   {pj} … <prefix>-base (SB_BASE_VMID) を full clone して SB_NET.0.(VMID - SB_VMID_BASE) で起動、ssh 待ち（9110 → 10.77.0.110）
#   finalize {pj} … シャットダウンして template 化
# PJ 固有の焼き込みは PJ 定義の provision.sh を VM 内で実行する（この script の外）。
set -euo pipefail
if [[ -z "${SB_TENANT_LOADED:-}" ]]; then _d="$(dirname "${BASH_SOURCE[0]:-.}")"; [[ -f "$_d/_tenant.sh" ]] && source "$_d/_tenant.sh" || { echo "[error] _tenant.sh が無い（sandbox/proxmox/run.sh 経由で実行する）" >&2; exit 1; }; fi
cmd="${1:?usage: 32-pj-template.sh create|finalize <pj>}"
PJ="${2:?pj slug required}"
BASE_VMID="${BASE_VMID:-$SB_BASE_VMID}"
VMID="${TPL_VMID:-$SB_TPL_BASE}"
NAME="$SB_PREFIX-tpl-$PJ"
# テンプレートの IP は VMID の帯内オフセットを SB_NET.0.x に写す（9110 → .110、9111 → .111。PJ テンプレートを並行して作れる）
IP="${SB_NET}.0.$((VMID - SB_VMID_BASE))"
MEM="${VM_MEMORY:-4096}"
CORES="${VM_CORES:-2}"
sb_tenant_summary
(( VMID - SB_VMID_BASE >= 101 && VMID - SB_VMID_BASE <= 199 )) || { echo "[error] TPL_VMID は SB_VMID_BASE+101..199 の範囲（IP を .0.x に写すため）: $VMID" >&2; exit 1; }

wait_ssh() {
  local ip=$1 i
  for i in $(seq 1 60); do
    if timeout 2 bash -c "</dev/tcp/$ip/22" 2>/dev/null; then echo "[ok] ssh port open on $ip"; return 0; fi
    sleep 5
  done
  echo "[error] ssh not reachable on $ip" >&2; return 1
}

case "$cmd" in
  create)
    qm config "$BASE_VMID" | grep -q '^template: 1' || { echo "[error] $BASE_VMID is not a template (Step 3 未完了)" >&2; exit 1; }
    if qm status "$VMID" >/dev/null 2>&1; then echo "[skip] VM $VMID exists"; exit 0; fi
    qm clone "$BASE_VMID" "$VMID" --name "$NAME" --full 1 --pool "$SB_POOL" \
      --description "aifactory sandbox PJ template: $PJ ($SB_TENANT; from $SB_PREFIX-base $BASE_VMID)" \
      || qm clone "$BASE_VMID" "$VMID" --name "$NAME" --full 1 --description "aifactory sandbox PJ template: $PJ (from $BASE_VMID)"
    qm set "$VMID" --memory "$MEM" --cores "$CORES" --ipconfig0 "ip=${IP}/16,gw=$SB_HOST_IP" >/dev/null
    fw_vm "$VMID"
    qm start "$VMID"
    wait_ssh "$IP"
    echo "[next] GH_TOKEN を渡して PJ 定義の provision.sh を dev@$IP で実行（BUILD.md Step 4）"
    ;;
  finalize)
    if qm config "$VMID" | grep -q '^template: 1'; then echo "[skip] already template"; exit 0; fi
    qm status "$VMID" | grep -q running && qm shutdown "$VMID" --timeout 180
    qm template "$VMID"
    echo "[ok] VM $VMID ($NAME) is now a template"
    ;;
  *) echo "usage: 32-pj-template.sh create|finalize <pj>" >&2; exit 1 ;;
esac
