#!/usr/bin/env bash
# console/bin/install.sh: Web コンソールを「いつでも開ける」状態にする（Mac ローカル）。
#   install.sh            ~/.local/bin/aifactory-console（symlink）だけ
#   install.sh --launchd  さらに launchd に登録して常駐（ログイン時に自動起動、落ちたら再起動）。http://127.0.0.1:8765/
#   install.sh --remove   launchd 登録と symlink を外す
# 常駐の python3 は「今のシェルの python3」を絶対パスで焼く（runner が使う yaml / jsonschema が入っているもの）。
# PATH も今のシェルから必要な分（python3 / claude / gh / jq / sandbox）を拾って plist に書く。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"     # console/
REPO="$(cd "$HERE/.." && pwd)"
LABEL=com.aifactory.console
PLIST_SRC="$HERE/launchd/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="${CONSOLE_PORT:-8765}"
BIN="$HOME/.local/bin"

log() { echo "[install] $*"; }

do_link() {
  mkdir -p "$BIN"; ln -sfn "$HERE/bin/console" "$BIN/aifactory-console"
  log "symlink: $BIN/aifactory-console -> $HERE/bin/console"
}

do_launchd() {
  local py path d
  py="$(python3 -c 'import sys; print(sys.executable)')"
  "$py" -c 'import yaml, jsonschema' 2>/dev/null || { echo "[install] warn: ${py} に yaml / jsonschema が無い。runner（kb run）がこの python3 で動かない" >&2; }
  path="$(dirname "$py")"
  for c in claude gh jq sandbox ssh git; do
    d="$(command -v "$c" 2>/dev/null || true)"; [[ -n "$d" ]] && d="$(cd "$(dirname "$d")" && pwd -P)" && [[ ":$path:" != *":$d:"* ]] && path="$path:$d"
  done
  for d in "$BIN" /opt/homebrew/bin /usr/local/bin /usr/bin /bin /usr/sbin /sbin; do [[ ":$path:" != *":$d:"* ]] && path="$path:$d"; done
  mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
  sed -e "s#@@PYTHON@@#$py#g" -e "s#@@REPO@@#$REPO#g" -e "s#@@PATH@@#$path#g" -e "s#@@HOME@@#$HOME#g" -e "s#@@PORT@@#$PORT#g" "$PLIST_SRC" > "$PLIST_DST"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
  log "launchd: $PLIST_DST を登録（python3=$py, port=${PORT}）"
  local i; for i in $(seq 1 20); do
    curl -sf "http://127.0.0.1:$PORT/api/overview" >/dev/null 2>&1 && { log "ok: http://127.0.0.1:$PORT/  ログ: ~/Library/Logs/aifactory-console.log"; return 0; }
    sleep 0.5
  done
  echo "[install] error: ${PORT} で応答しない。~/Library/Logs/aifactory-console.log を見る" >&2; return 1
}

do_remove() {
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && log "launchd: $LABEL を外した" || log "launchd: 登録は無かった"
  rm -f "$PLIST_DST" "$BIN/aifactory-console"; log "removed: $PLIST_DST, $BIN/aifactory-console"
}

case "${1:-}" in
  "") do_link ;;
  --launchd) do_link; do_launchd ;;
  --remove) do_remove ;;
  *) echo "usage: install.sh [--launchd|--remove]" >&2; exit 1 ;;
esac
