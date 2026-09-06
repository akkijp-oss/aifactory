#!/usr/bin/env bash
# 40-pool.sh: PJ テンプレートから貸出用 VM を N 台増やす（BUILD.md Step 5a）。
#   40-pool.sh <pj> <count>
#   env: TPL_VMID (既定 9110), VM_MEMORY (4096), VM_CORES (2), APP_WAIT_TRIES (:3000 待ち回数×5秒。既定 36。アプリ無しの汎用プールは 0)
# 1台ごと: linked clone → sb-{pj}-NN（NN は PJ 内の連番）/ IP は SB_NET.1.(VMID-SB_POOL_BASE)（PJ 横断で一意。既定 10.77.1.(VMID-9200)）→ 起動 → ssh 待ち → :3000 応答待ち → snapshot clean (RAM 込み)
# → sb-gw の /etc/sandbox/hosts に常設名を登録
set -euo pipefail

PJ="${1:?usage: 40-pool.sh <pj> <count>}"
COUNT="${2:?count required}"
TPL="${TPL_VMID:-9110}"
MEM="${VM_MEMORY:-4096}"
CORES="${VM_CORES:-2}"
APP_WAIT_TRIES="${APP_WAIT_TRIES:-36}"
SB_NET="${SB_NET:-10.77}"             # sandbox ネットワークの /16 プレフィックス
SB_GW_CT="${SB_GW_CT:-9000}"          # ゲートウェイ LXC の VMID
SB_POOL_BASE="${SB_POOL_BASE:-9200}"  # プール VM の VMID 起点（Mac 側 sandbox CLI の SB_POOL_BASE と揃える）
GW_CT="$SB_GW_CT"
POOL_BASE="$SB_POOL_BASE"

qm config "$TPL" | grep -q '^template: 1' || { echo "[error] $TPL is not a template" >&2; exit 1; }

wait_port() {
  local ip=$1 port=$2 tries=${3:-60} i
  for i in $(seq 1 "$tries"); do
    if timeout 2 bash -c "</dev/tcp/$ip/$port" 2>/dev/null; then return 0; fi
    sleep 5
  done
  return 1
}

# 既存の sb-{pj}-NN から次番号を決める
existing="$(qm list | awk -v p="sb-$PJ-" '$2 ~ "^"p {print $2}' | sed -E "s/^sb-$PJ-//" | sort -n | tail -1)"
next=$(( ${existing:-0} + 1 ))

for _ in $(seq 1 "$COUNT"); do
  NN=$(printf '%02d' "$next")
  NAME="sb-$PJ-$NN"
  # VMID: POOL_BASE+1（既定 9201）から空きを探す（PJ 横断）。IP は VMID から導く（SB_NET.1.1〜99）
  VMID=$(( POOL_BASE + 1 ))
  while qm status "$VMID" >/dev/null 2>&1 || pct status "$VMID" >/dev/null 2>&1; do VMID=$((VMID+1)); done
  (( VMID < POOL_BASE + 100 )) || { echo "[error] VMID exhausted ($POOL_BASE-$((POOL_BASE+99)))" >&2; exit 1; }
  IP="${SB_NET}.1.$((VMID-POOL_BASE))"

  echo "=== $NAME (vmid $VMID, $IP)"
  qm clone "$TPL" "$VMID" --name "$NAME" --full 0 --description "aifactory sandbox pool: $PJ #$NN (linked clone of $TPL)"
  qm set "$VMID" --memory "$MEM" --cores "$CORES" --onboot 0 --ipconfig0 "ip=${IP}/16,gw=${SB_NET}.0.1" >/dev/null
  # 通信をインターネットと sb-gw の DNS に絞る（ADR-0010。group sandbox は 50-firewall.sh が cluster.fw に定義）
  net0="$(qm config "$VMID" | sed -n 's/^net0: //p')"; [[ "$net0" == *firewall=1* ]] || qm set "$VMID" --net0 "${net0},firewall=1" >/dev/null
  printf '[OPTIONS]\nenable: 1\npolicy_in: DROP\npolicy_out: ACCEPT\n\n[RULES]\nGROUP sandbox\n' > "/etc/pve/firewall/$VMID.fw"
  qm start "$VMID"
  wait_port "$IP" 22 || { echo "[error] $NAME: ssh not up" >&2; exit 1; }
  echo "[ok] ssh up"
  if (( APP_WAIT_TRIES == 0 )); then
    echo "[info] APP_WAIT_TRIES=0: :3000 待ちをスキップ（アプリ無しテンプレート）"
  elif wait_port "$IP" 3000 "$APP_WAIT_TRIES"; then
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://$IP:3000/" || true)"
    echo "[ok] app :3000 responded ($code)"
  else
    echo "[warn] :3000 not responding after 3min. PJ テンプレートの sandbox-app.service を確認。スナップショットは取る"
  fi
  sleep 10   # 起動直後の書き込みが落ち着くのを待つ
  qm snapshot "$VMID" clean --vmstate 1 --description "aifactory: clean state for take/reset ($(date -Iseconds))"
  echo "[ok] snapshot clean (with RAM)"

  # 常設 DNS 名
  pct exec "$GW_CT" -- bash -c "sed -i '/ $NAME\$/d' /etc/sandbox/hosts; echo '$IP $NAME' >> /etc/sandbox/hosts; systemctl reload dnsmasq"
  echo "[ok] dns $NAME.sb.internal -> $IP"
  next=$((next+1))
done

echo "--- pool"
qm list | awk -v p="sb-$PJ-" 'NR==1 || $2 ~ "^"p'
