#!/usr/bin/env bash
# 45-pool-keys.sh: 既存のプール VM に公開鍵（SB_PUBKEY = メンテナの鍵 + 制御系 LXC の鍵）を後から入れる（BUILD.md Step 2d の注意、ADR-0017）。
#   テンプレートを焼き直さずに、制御系 LXC が既存のプール VM へ ssh できるようにする。
#   1台ごと: cloud-init の sshkeys を更新（config に残る）→ qemu-guest-agent 経由で dev の authorized_keys に即時追記 → snapshot clean を取り直す
#   貸出中（LENT に vmid を列挙）は飛ばす。VM は clean 直後の状態（release 済み）である前提。
#   使い方: LENT="9204" sandbox/proxmox/run.sh 45-pool-keys.sh [pj]     （pj を与えるとその PJ のプールだけ）
set -euo pipefail
if [[ -z "${SB_TENANT_LOADED:-}" ]]; then _d="$(dirname "${BASH_SOURCE[0]:-.}")"; [[ -f "$_d/_tenant.sh" ]] && source "$_d/_tenant.sh" || { echo "[error] _tenant.sh が無い（sandbox/proxmox/run.sh 経由で実行する）" >&2; exit 1; }; fi
: "${SB_PUBKEY:?SB_PUBKEY が必要。run.sh 経由で実行する}"
LENT="${LENT:-}"
ONLY_PJ="${1:-}"
sb_tenant_summary
printf '%s\n' "$SB_PUBKEY" > /tmp/sb.pub
keys_b64="$(printf '%s\n' "$SB_PUBKEY" | base64 -w0)"

while read -r id type name; do
  [[ "$type" == qemu ]] || continue
  qm config "$id" | grep -q '^template: 1' && continue
  [[ "$name" == "$SB_PREFIX-"* ]] || continue
  [[ -n "$ONLY_PJ" && "$name" != "$SB_PREFIX-$ONLY_PJ-"* ]] && continue
  if [[ " $LENT " == *" $id "* ]]; then echo "[skip] $id $name (貸出中)"; continue; fi
  qm status "$id" | grep -q running || { echo "[skip] $id $name (停止中。起動してから)"; continue; }
  qm set "$id" --sshkeys /tmp/sb.pub >/dev/null
  # guest agent で root として実行（ssh 不要）。既にある鍵は足さない
  if qm guest exec "$id" --timeout 30 -- bash -c "install -d -m 700 -o dev -g dev /home/dev/.ssh; touch /home/dev/.ssh/authorized_keys; chown dev:dev /home/dev/.ssh/authorized_keys; chmod 600 /home/dev/.ssh/authorized_keys; echo '$keys_b64' | base64 -d | while IFS= read -r k; do [ -n \"\$k\" ] && { grep -qxF \"\$k\" /home/dev/.ssh/authorized_keys || echo \"\$k\" >> /home/dev/.ssh/authorized_keys; }; done; wc -l < /home/dev/.ssh/authorized_keys" >/tmp/.sb-exec.json 2>&1; then
    n="$(python3 -c 'import json,sys; d=json.load(open("/tmp/.sb-exec.json")); print((d.get("out-data") or "").strip())' 2>/dev/null || echo "?")"
    echo "[ok] $id $name: authorized_keys $n 行"
  else
    echo "[warn] $id $name: guest exec に失敗（qemu-guest-agent が動いていない?）: $(head -c 200 /tmp/.sb-exec.json)"; continue
  fi
  if qm listsnapshot "$id" | grep -q '^`-> clean\|clean '; then
    qm delsnapshot "$id" clean >/dev/null
    qm snapshot "$id" clean --vmstate 1 --description "aifactory: clean state for take/reset, keys updated ($(date -Iseconds))" >/dev/null
    echo "[ok] $id $name: snapshot clean 取り直し"
  else
    echo "[warn] $id $name: snapshot clean が無い"
  fi
done < <(pool_members)
