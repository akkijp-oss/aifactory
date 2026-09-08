#!/usr/bin/env bash
# sandbox/bin/install.sh: sandbox CLI を ~/.local/bin に実体コピーする（BUILD.md Step 5b）。
#   install.sh            ~/.local/bin/sandbox（実体コピー）
#   install.sh --systemd  さらに常駐の timer を systemd に登録する（Linux。制御系 LXC 用。ADR-0017）
#     aifactory-gh-refresh  GitHub App トークンの更新（45 分ごと）
#     aifactory-idle-stop   使われていないプール VM の停止（15 分ごと。252）
#   install.sh --remove   systemd の登録を外す
#   macOS: シンボリックリンクだと launchd（gh-refresh）の bash が Documents 配下を読めず "Operation not permitted" になる（TCC）ので実体コピー。
#   launchd の plist は sandbox/templates/launchd/（BUILD.md Step 0b）。リポジトリの sandbox/bin/sandbox を更新したら、もう一度これを実行する
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HOME/.local/bin"
UNIT_DIR="${SANDBOX_UNIT_DIR:-/etc/systemd/system}"   # テストから差し替える
# 登録する常駐。1 つにつき .service と .timer が sandbox/templates/systemd/ にある
UNITS=(aifactory-gh-refresh aifactory-idle-stop)
SUDO=""; [[ $EUID -eq 0 ]] || SUDO="sudo"

do_copy() {
  rm -f "$HOME/.local/bin/sandbox"
  install -m 755 "$HERE/sandbox" "$HOME/.local/bin/sandbox"
  echo "[ok] installed $HOME/.local/bin/sandbox ($(sed -n 2p "$HERE/sandbox" | cut -c1-40)...)"
}
do_systemd() {
  command -v systemctl >/dev/null || { echo "[error] systemd が無い（macOS は launchd: sandbox/templates/launchd/）" >&2; exit 1; }
  local path="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" u f
  for u in "${UNITS[@]}"; do
    for f in "$u.service" "$u.timer"; do
      sed -e "s#@@USER@@#$(id -un)#g" -e "s#@@HOME@@#$HOME#g" -e "s#@@PATH@@#$path#g" "$HERE/../templates/systemd/$f" | $SUDO tee "$UNIT_DIR/$f" >/dev/null
    done
  done
  $SUDO systemctl daemon-reload
  for u in "${UNITS[@]}"; do $SUDO systemctl enable --now "$u.timer" >/dev/null; done
  echo "[ok] systemd: aifactory-gh-refresh.timer（45 分ごと）/ aifactory-idle-stop.timer（15 分ごと。使われていない VM を止める）"
  echo "     ログ: journalctl -u aifactory-gh-refresh -u aifactory-idle-stop"
}
do_remove() {
  local u
  for u in "${UNITS[@]}"; do
    $SUDO systemctl disable --now "$u.timer" 2>/dev/null || true
    $SUDO rm -f "$UNIT_DIR/$u.service" "$UNIT_DIR/$u.timer"
  done
  $SUDO systemctl daemon-reload
  echo "[ok] removed ${UNITS[*]} timers"
}
case "${1:-}" in
  "") do_copy ;;
  --systemd) do_copy; do_systemd ;;
  --remove) do_remove ;;
  *) echo "usage: install.sh [--systemd|--remove]" >&2; exit 1 ;;
esac
