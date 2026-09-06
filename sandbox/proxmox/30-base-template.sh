#!/usr/bin/env bash
# 30-base-template.sh: ベーステンプレート `sb-base`（VMID は SB_BASE_VMID、既定 9100）を作る（BUILD.md Step 3）。
#   create   … cloud image から VM を作り SB_NET.0.100（既定 10.77.0.100）で起動、ssh 待ち
#   finalize … シャットダウンして template 化
# 焼き込み本体は 31-provision-base.sh（VM の中で実行する）。
set -euo pipefail

SB_NET="${SB_NET:-10.77}"             # sandbox ネットワークの /16 プレフィックス
SB_BASE_VMID="${SB_BASE_VMID:-9100}"  # ベーステンプレートの VMID
VMID="${TPL_VMID:-$SB_BASE_VMID}"
NAME=sb-base
IP="${SB_NET}.0.100"
IMG=noble-server-cloudimg-amd64.img
IMG_URL="https://cloud-images.ubuntu.com/noble/current/$IMG"
MEM="${VM_MEMORY:-4096}"
CORES="${VM_CORES:-2}"
DISK=40G

cmd="${1:?usage: 30-base-template.sh create|finalize}"

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
    : "${SB_PUBKEY:?SB_PUBKEY が必要。run.sh 経由で実行する}"
    if qm status "$VMID" >/dev/null 2>&1; then
      echo "[skip] VM $VMID exists ($(qm status $VMID))"; exit 0
    fi
    mkdir -p /root/images
    [[ -f "/root/images/$IMG" ]] || { echo "[info] downloading $IMG"; wget -q -O "/root/images/$IMG" "$IMG_URL"; }
    printf '%s\n' "$SB_PUBKEY" > /tmp/sb.pub

    qm create "$VMID" --name "$NAME" --memory "$MEM" --cores "$CORES" --cpu host --ostype l26 \
      --scsihw virtio-scsi-single --agent enabled=1 --serial0 socket --vga serial0 \
      --net0 virtio,bridge=sbnet \
      --description "aifactory sandbox base template (Ubuntu 24.04 + mise/node/pg/redis/chrome/gh/claude)"
    qm set "$VMID" --scsi0 "local-lvm:0,import-from=/root/images/$IMG,discard=on,ssd=1,iothread=1"
    qm disk resize "$VMID" scsi0 "$DISK"
    # 通信制限（ADR-0010）: net0 firewall=1 + group sandbox。clone 先にも引き継がれる
    net0="$(qm config "$VMID" | sed -n 's/^net0: //p')"; [[ "$net0" == *firewall=1* ]] || qm set "$VMID" --net0 "${net0},firewall=1" >/dev/null
    printf '[OPTIONS]\nenable: 1\npolicy_in: DROP\npolicy_out: ACCEPT\n\n[RULES]\nGROUP sandbox\n' > "/etc/pve/firewall/$VMID.fw"
    qm set "$VMID" --ide2 local-lvm:cloudinit --boot order=scsi0 \
      --ciuser dev --ciupgrade 0 --sshkeys /tmp/sb.pub \
      --ipconfig0 "ip=${IP}/16,gw=${SB_NET}.0.1" --nameserver "${SB_NET}.0.2" --searchdomain sb.internal
    qm start "$VMID"
    wait_ssh "$IP"
    echo "[next] scp 31-provision-base.sh → dev@$IP:/tmp/ して sudo bash で実行（BUILD.md Step 3b）"
    ;;
  finalize)
    if qm config "$VMID" | grep -q '^template: 1'; then echo "[skip] already template"; exit 0; fi
    qm status "$VMID" | grep -q running && qm shutdown "$VMID" --timeout 180
    qm template "$VMID"
    echo "[ok] VM $VMID is now a template"
    qm config "$VMID" | grep -E "^(template|name|memory|cores|agent|ide2|scsi0|net0)"
    ;;
  *) echo "usage: 30-base-template.sh create|finalize" >&2; exit 1 ;;
esac
