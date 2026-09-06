#!/usr/bin/env bash
# run.sh: proxmox/ 配下のスクリプトを Proxmox ホスト上で実行する入口。
#   sandbox/proxmox/run.sh 10-sdn.sh
#   sandbox/proxmox/run.sh 30-base-template.sh create
#   TPL_VMID=9111 sandbox/proxmox/run.sh 40-pool.sh ba 3
#
# - Mac 側の公開鍵（VM / LXC に入れる）を SB_PUBKEY として渡す
# - ホスト名は PVE_HOST（ssh のエイリアスかホスト名。必須、既定値なし）
# - sandbox ネットワークの設計値（SB_NODE / SB_NET / SB_GW_CT / SB_BASE_VMID / SB_POOL_BASE）は環境にあればそのまま先へ渡す（未設定なら各スクリプトの既定）
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ~/.config/sandbox/env があれば PVE_HOST と SB_* を補う（シェルで明示した環境変数が優先）
ENV_FILE="${SANDBOX_ENV_FILE:-$HOME/.config/sandbox/env}"
if [[ -f "$ENV_FILE" ]]; then
  for v in PVE_HOST SB_NODE SB_NET SB_GW_CT SB_BASE_VMID SB_POOL_BASE; do
    # shellcheck disable=SC1090
    [[ -n "${!v:-}" ]] || printf -v "$v" '%s' "$(source "$ENV_FILE" >/dev/null 2>&1; printf '%s' "${!v:-}")"
  done
fi
PVE_HOST="${PVE_HOST:-}"
[[ -n "$PVE_HOST" ]] || { echo "[error] PVE_HOST が未設定。Proxmox ホストの ssh エイリアスかホスト名を環境変数か ~/.config/sandbox/env で指定する（sandbox/templates/env.example 参照）" >&2; exit 1; }
PUBKEY_FILE="${SB_PUBKEY_FILE:-$HOME/.ssh/conf.d/aifactory/sb_ed25519.pub}"

script="${1:?usage: run.sh <script> [args...]}"; shift
[[ -f "$HERE/$script" ]] || { echo "[error] no such script: $HERE/$script" >&2; exit 1; }
[[ -f "$PUBKEY_FILE" ]] || { echo "[error] public key not found: $PUBKEY_FILE (BUILD.md Step 0)" >&2; exit 1; }

SB_PUBKEY="$(cat "$PUBKEY_FILE")"
# 引数と環境を安全にクォートして渡す
q() { printf '%q ' "$@"; }
remote_env="SB_PUBKEY=$(q "$SB_PUBKEY")TPL_VMID=$(q "${TPL_VMID:-}")VM_MEMORY=$(q "${VM_MEMORY:-}")VM_CORES=$(q "${VM_CORES:-}")APP_WAIT_TRIES=$(q "${APP_WAIT_TRIES:-}")LENT=$(q "${LENT:-}")"
remote_env+="SB_NODE=$(q "${SB_NODE:-}")SB_NET=$(q "${SB_NET:-}")SB_GW_CT=$(q "${SB_GW_CT:-}")SB_BASE_VMID=$(q "${SB_BASE_VMID:-}")SB_POOL_BASE=$(q "${SB_POOL_BASE:-}")"

echo "[run] $script $* on $PVE_HOST" >&2
exec ssh -o BatchMode=yes "$PVE_HOST" "$remote_env bash -s -- $(q "$@")" < "$HERE/$script"
