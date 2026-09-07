#!/usr/bin/env bash
# sandbox/bin/install.sh: sandbox CLI を ~/.local/bin に実体コピーする（BUILD.md Step 5b）。
#   install.sh            ~/.local/bin/sandbox（実体コピー）
#   install.sh --systemd  さらに GitHub App トークン更新の timer（45 分ごと）を systemd に登録する（Linux。制御系 LXC 用。ADR-0017）
#   install.sh --remove   systemd の登録を外す
#   macOS: シンボリックリンクだと launchd（gh-refresh）の bash が Documents 配下を読めず "Operation not permitted" になる（TCC）ので実体コピー。
#   launchd の plist は sandbox/templates/launchd/（BUILD.md Step 0b）。リポジトリの sandbox/bin/sandbox を更新したら、もう一度これを実行する
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HOME/.local/bin"
UNIT_DIR=/etc/systemd/system
SUDO=""; [[ $EUID -eq 0 ]] || SUDO="sudo"

do_copy() {
  rm -f "$HOME/.local/bin/sandbox"
  install -m 755 "$HERE/sandbox" "$HOME/.local/bin/sandbox"
  echo "[ok] installed $HOME/.local/bin/sandbox ($(sed -n 2p "$HERE/sandbox" | cut -c1-40)...)"
}
do_systemd() {
  command -v systemctl >/dev/null || { echo "[error] systemd が無い（macOS は launchd: sandbox/templates/launchd/）" >&2; exit 1; }
  local path="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
  for f in aifactory-gh-refresh.service aifactory-gh-refresh.timer; do
    sed -e "s#@@USER@@#$(id -un)#g" -e "s#@@HOME@@#$HOME#g" -e "s#@@PATH@@#$path#g" "$HERE/../templates/systemd/$f" | $SUDO tee "$UNIT_DIR/$f" >/dev/null
  done
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now aifactory-gh-refresh.timer >/dev/null
  echo "[ok] systemd: aifactory-gh-refresh.timer（45 分ごと。journalctl -u aifactory-gh-refresh）"
}
do_remove() {
  $SUDO systemctl disable --now aifactory-gh-refresh.timer 2>/dev/null || true
  $SUDO rm -f "$UNIT_DIR/aifactory-gh-refresh.service" "$UNIT_DIR/aifactory-gh-refresh.timer"; $SUDO systemctl daemon-reload
  echo "[ok] removed aifactory-gh-refresh timer"
}
case "${1:-}" in
  "") do_copy ;;
  --systemd) do_copy; do_systemd ;;
  --remove) do_remove ;;
  *) echo "usage: install.sh [--systemd|--remove]" >&2; exit 1 ;;
esac
