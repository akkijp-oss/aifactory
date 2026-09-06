#!/usr/bin/env bash
# bin/migrate-workspace.sh: 旧配置（リポジトリ内に運用データを置く）から workspace 配置（ADR-0016）へ移す。1 回だけ実行する。
#   bin/migrate-workspace.sh [--dry-run]
#
# 移すもの（git 追跡からも外す。ファイルは消さない）:
#   sandbox/templates/<pj>/   → $AIFACTORY_WORKSPACE/projects/<pj>/   （provision.sh か project.yml があるディレクトリ）
#   kanban/{kanban.db,tickets/,BOARD.md} → $AIFACTORY_WORKSPACE/kanban/
#   workflow/runs/*           → $AIFACTORY_WORKSPACE/runs/
#   glue/{intake,dispatch}.log → $AIFACTORY_WORKSPACE/logs/
#   workflow/prompts/         → $AIFACTORY_WORKSPACE/prompts/
#   docs/infra/, docs/source/*.transcript.md, docs/source/media/ → $AIFACTORY_WORKSPACE/docs/…（私有の資料）
# 前提: runner（workflow/bin/run）が動いていないこと。動いていれば止まる（run が旧パスへ書き続けるため）。
# 終わったら: console を再起動（launchctl kickstart -k gui/$(id -u)/com.aifactory.console）、sandbox/bin/install.sh で CLI を更新。
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WS="${AIFACTORY_WORKSPACE:-$REPO/workspace}"
DRY=0; [[ "${1:-}" == "--dry-run" ]] && DRY=1
cd "$REPO"

if ps -axo pid=,command= | grep -F "workflow/bin/run " | grep -v grep >/dev/null; then
  echo "[$( (( DRY )) && echo warn || echo error )] runner が実行中。終わってから実行する:" >&2; ps -axo pid=,command= | grep -F "workflow/bin/run " | grep -v grep >&2
  (( DRY )) || exit 1
fi

run() { if (( DRY )); then echo "  (dry) $*"; else "$@"; fi; }
untrack() { git ls-files --error-unmatch -- "$1" >/dev/null 2>&1 && run git rm -r -q --cached -- "$1" || true; }
move() { # src dst
  local src=$1 dst=$2
  [[ -e "$src" ]] || return 0
  if [[ -e "$dst" ]]; then echo "[skip] $dst は既にある（$src はそのまま）"; return 0; fi
  echo "[move] $src → $dst"; untrack "$src"; run mkdir -p "$(dirname "$dst")"; run mv "$src" "$dst"
}

echo "== workspace: $WS"
run mkdir -p "$WS"/{projects,kanban,runs,logs,docs}

echo "== PJ 定義"
for d in sandbox/templates/*/; do
  d="${d%/}"; pj="$(basename "$d")"
  [[ -f "$d/provision.sh" || -f "$d/project.yml" ]] || continue
  move "$d" "$WS/projects/$pj"
done

echo "== kanban"
for f in kanban.db tickets attachments BOARD.md; do move "kanban/$f" "$WS/kanban/$f"; done

echo "== runs"
if [[ -d workflow/runs ]]; then
  for r in workflow/runs/* workflow/runs/.[!.]*; do [[ -e "$r" ]] && move "$r" "$WS/runs/$(basename "$r")"; done
  (( DRY )) || rmdir workflow/runs 2>/dev/null || true
fi

echo "== logs / prompts"
for f in intake.log dispatch.log; do move "glue/$f" "$WS/logs/$f"; done
move workflow/prompts "$WS/prompts"

echo "== 私有の資料"
move docs/infra "$WS/docs/infra"
for f in docs/source/*.transcript.md; do [[ -e "$f" ]] && move "$f" "$WS/docs/source/$(basename "$f")"; done
move docs/source/media "$WS/docs/source/media"

if (( DRY )); then echo "== dry-run 終了（何も動かしていない）"; exit 0; fi
echo "== 置き場の確認"; python3 lib/aifactory_paths.py
echo "== ボード再生成"; kanban/bin/kb render
cat <<MSG
== 完了。次にやること
  1. console を再起動: launchctl kickstart -k gui/\$(id -u)/com.aifactory.console
  2. sandbox CLI を更新: sandbox/bin/install.sh
  3. git status で「削除（追跡から外した）」を確認してコミット
MSG
