#!/usr/bin/env bash
# 32-pj-template.sh: PJ テンプレート `sb-tpl-{pj}`（VMID は TPL_VMID、911x を PJ ごとに割る）を作る（BUILD.md Step 4）。
#   create   {pj} … sb-base (SB_BASE_VMID、既定 9100) を full clone して SB_NET.0.(VMID-9000) で起動、ssh 待ち（9110 → 10.77.0.110）
#   finalize {pj} … シャットダウンして template 化
# PJ 固有の焼き込みは templates/{pj}/provision.sh を VM 内で実行する（この script の外）。
set -euo pipefail

cmd="${1:?usage: 32-pj-template.sh create|finalize <pj>}"
PJ="${2:?pj slug required}"
SB_NET="${SB_NET:-10.77}"             # sandbox ネットワークの /16 プレフィックス
SB_BASE_VMID="${SB_BASE_VMID:-9100}"  # ベーステンプレートの VMID
BASE_VMID="${BASE_VMID:-$SB_BASE_VMID}"
VMID="${TPL_VMID:-9110}"
NAME="sb-tpl-$PJ"
# テンプレートの IP は VMID の下 3 桁を SB_NET.0.x に写す（9110 → .110、9111 → .111。PJ テンプレートを並行して作れる）。
# 9000 は「VMID 9xxx 帯の起点」であって SB_GW_CT とは無関係なので、ここでは固定値のまま
IP="${SB_NET}.0.$((VMID-9000))"
MEM="${VM_MEMORY:-4096}"
CORES="${VM_CORES:-2}"

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
    qm clone "$BASE_VMID" "$VMID" --name "$NAME" --full 1 \
      --description "aifactory sandbox PJ template: $PJ (from sb-base $BASE_VMID)"
    qm set "$VMID" --memory "$MEM" --cores "$CORES" --ipconfig0 "ip=${IP}/16,gw=${SB_NET}.0.1" >/dev/null
    # 通信制限（ADR-0010）: net0 firewall=1 + group sandbox。clone 先にも引き継がれる
    net0="$(qm config "$VMID" | sed -n 's/^net0: //p')"; [[ "$net0" == *firewall=1* ]] || qm set "$VMID" --net0 "${net0},firewall=1" >/dev/null
    printf '[OPTIONS]\nenable: 1\npolicy_in: DROP\npolicy_out: ACCEPT\n\n[RULES]\nGROUP sandbox\n' > "/etc/pve/firewall/$VMID.fw"
    qm start "$VMID"
    wait_ssh "$IP"
    echo "[next] GH_TOKEN を渡して templates/$PJ/provision.sh を dev@$IP で実行（BUILD.md Step 4）"
    ;;
  finalize)
    if qm config "$VMID" | grep -q '^template: 1'; then echo "[skip] already template"; exit 0; fi
    qm status "$VMID" | grep -q running && qm shutdown "$VMID" --timeout 180
    qm template "$VMID"
    echo "[ok] VM $VMID ($NAME) is now a template"
    ;;
  *) echo "usage: 32-pj-template.sh create|finalize <pj>" >&2; exit 1 ;;
esac
