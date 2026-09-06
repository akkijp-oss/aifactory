#!/usr/bin/env bash
# bin/install-hooks.sh: 公開事故を防ぐ git hook（.githooks/）をこの clone に有効化する。
#   .git/hooks/{pre-commit,pre-push} → ../../.githooks/… の symlink を張る（`git config core.hooksPath .githooks` でも同じ）
#   gitleaks が PATH に無ければ入れ方を案内する
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
hooks_dir="$(git -C "$REPO" rev-parse --git-path hooks)"
mkdir -p "$hooks_dir"
for h in pre-commit pre-push; do
  ln -sfn "../../.githooks/$h" "$hooks_dir/$h"; echo "[hooks] $hooks_dir/$h -> .githooks/$h"
done
chmod +x "$REPO"/.githooks/*
if command -v gitleaks >/dev/null; then echo "[hooks] gitleaks $(gitleaks version)"; else
  echo "[hooks] warn: gitleaks が無い。https://github.com/gitleaks/gitleaks/releases からバイナリを ~/.local/bin に置く（brew install gitleaks でも可）"; fi
