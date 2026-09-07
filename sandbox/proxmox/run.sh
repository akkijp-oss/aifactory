#!/usr/bin/env bash
# run.sh: proxmox/ 配下のスクリプトを Proxmox ホスト上で実行する入口（メンテナ = ホスト管理者の操作）。
#   sandbox/proxmox/run.sh 10-sdn.sh
#   sandbox/proxmox/run.sh 30-base-template.sh create
#   TPL_VMID=9111 sandbox/proxmox/run.sh 40-pool.sh ba 3
#   SB_TENANT=acme sandbox/proxmox/run.sh 05-tenant.sh        # 別テナント（~/.config/sandbox/tenants/acme.env を読む）
#
# - 公開鍵（VM / LXC に入れる）を SB_PUBKEY として渡す。メンテナの鍵に加え、そのテナントの制御系 LXC（<prefix>-ctl）が
#   既にあればその鍵も足す（制御系が VM と sb-gw に ssh できるように。ADR-0017）
# - ホスト名は PVE_HOST（ssh のエイリアスかホスト名。必須、既定値なし）
# - テナントの設計値（SB_TENANT / SB_NET / SB_VMID_BASE と、個別上書きの SB_ZONE …）は環境にあればそのまま先へ渡す。
#   導出は _tenant.sh（スクリプトの前に連結して送る）
# - 設定ファイル: SANDBOX_ENV_FILE → SB_TENANT が default 以外なら ~/.config/sandbox/tenants/<tenant>.env → ~/.config/sandbox/env
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SB_TENANT="${SB_TENANT:-default}"
ENV_FILE="${SANDBOX_ENV_FILE:-}"
if [[ -z "$ENV_FILE" ]]; then
  if [[ "$SB_TENANT" != default && -f "$HOME/.config/sandbox/tenants/$SB_TENANT.env" ]]; then ENV_FILE="$HOME/.config/sandbox/tenants/$SB_TENANT.env"
  else ENV_FILE="$HOME/.config/sandbox/env"; fi
fi
TENANT_VARS=(SB_TENANT SB_NET SB_VMID_BASE SB_NODE SB_ZONE SB_VNET SB_PREFIX SB_FW_GROUP SB_POOL SB_PVE_USER SB_DOMAIN SB_GW_CT SB_CTL_CT SB_BASE_VMID SB_TPL_BASE SB_POOL_BASE SB_WORKER_PORT)
if [[ -f "$ENV_FILE" ]]; then
  for v in PVE_HOST "${TENANT_VARS[@]}"; do
    # shellcheck disable=SC1090
    [[ -n "${!v:-}" ]] || printf -v "$v" '%s' "$(source "$ENV_FILE" >/dev/null 2>&1; printf '%s' "${!v:-}")"
  done
fi
PVE_HOST="${PVE_HOST:-}"
[[ -n "$PVE_HOST" ]] || { echo "[error] PVE_HOST が未設定。Proxmox ホストの ssh エイリアスかホスト名を環境変数か $ENV_FILE で指定する（sandbox/templates/env.example 参照）" >&2; exit 1; }
PUBKEY_FILE="${SB_PUBKEY_FILE:-$HOME/.ssh/conf.d/aifactory/sb_ed25519.pub}"

script="${1:?usage: run.sh <script> [args...]}"; shift
[[ -f "$HERE/$script" ]] || { echo "[error] no such script: $HERE/$script" >&2; exit 1; }
[[ -f "$PUBKEY_FILE" ]] || { echo "[error] public key not found: $PUBKEY_FILE (BUILD.md Step 0)" >&2; exit 1; }

SB_PUBKEY="$(cat "$PUBKEY_FILE")"
# 制御系 LXC が既にあれば、その鍵も VM / gw に入れる（無ければ静かに飛ばす。25-control-lxc.sh 自身の実行時も同じ）
ctl_ct="${SB_CTL_CT:-$(( ${SB_VMID_BASE:-9000} + 1 ))}"
ctl_key="$(ssh -o BatchMode=yes "$PVE_HOST" "pct exec $ctl_ct -- cat /home/aifactory/.ssh/conf.d/aifactory/sb_ed25519.pub 2>/dev/null" 2>/dev/null || true)"
[[ -n "$ctl_key" ]] && SB_PUBKEY+=$'\n'"$ctl_key"

# AIFACTORY_LOCAL_TREE=1: 手元の作業ツリー（git 追跡 + 未追跡、除外は .gitignore）を tar にしてホストへ置く。25-control-lxc.sh が
#   公開リポジトリの checkout の上に展開する（未 push の変更で制御系を作る・更新するとき。ADR-0017）
if [[ "${AIFACTORY_LOCAL_TREE:-}" == 1 ]]; then
  repo="$(cd "$HERE/../.." && pwd)"
  tree_tgz="$(mktemp -t aifactory-tree).tgz"
  (cd "$repo" && git ls-files -co --exclude-standard -z | grep -zv '^website/site/' | tar -cz --null -T - -f "$tree_tgz")
  scp -q -o BatchMode=yes "$tree_tgz" "$PVE_HOST:/tmp/aifactory-tree.tgz" && rm -f "$tree_tgz"
  echo "[run] local tree → $PVE_HOST:/tmp/aifactory-tree.tgz ($(cd "$repo" && git rev-parse --short HEAD) + 作業中の変更)" >&2
fi

# 引数と環境を安全にクォートして渡す
q() { printf '%q ' "$@"; }
remote_env="SB_PUBKEY=$(q "$SB_PUBKEY")TPL_VMID=$(q "${TPL_VMID:-}")VM_MEMORY=$(q "${VM_MEMORY:-}")VM_CORES=$(q "${VM_CORES:-}")APP_WAIT_TRIES=$(q "${APP_WAIT_TRIES:-}")LENT=$(q "${LENT:-}")"
remote_env+="AIFACTORY_REPO_URL=$(q "${AIFACTORY_REPO_URL:-}")AIFACTORY_REF=$(q "${AIFACTORY_REF:-}")AIFACTORY_LOCAL_TREE=$(q "${AIFACTORY_LOCAL_TREE:-}")"
for v in "${TENANT_VARS[@]}"; do remote_env+="$v=$(q "${!v:-}")"; done

echo "[run] $script $* on $PVE_HOST (tenant: $SB_TENANT, env: $ENV_FILE${ctl_key:+, +ctl key})" >&2
exec ssh -o BatchMode=yes "$PVE_HOST" "$remote_env bash -s -- $(q "$@")" < <(cat "$HERE/_tenant.sh" "$HERE/$script")
