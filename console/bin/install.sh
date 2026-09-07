#!/usr/bin/env bash
# console/bin/install.sh: Web コンソールを「いつでも開ける」状態にする。
#   install.sh            ~/.local/bin/aifactory-console（symlink）だけ
#   install.sh --launchd  さらに launchd に登録して常駐（macOS。ログイン時に自動起動、落ちたら再起動）。http://127.0.0.1:8765/
#   install.sh --systemd  systemd に登録して常駐（Linux。制御系 LXC 用。ADR-0017）。CONSOLE_HOST / CONSOLE_PORT で bind 先（既定 127.0.0.1:8765）。
#                         127.0.0.1 以外に bind するときは ~/.config/aifactory/ctl.env に CONSOLE_TOKEN が要る（console が拒む）
#   install.sh --remove   launchd / systemd の登録と symlink を外す
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
  local ws="${AIFACTORY_WORKSPACE:-$REPO/workspace}"   # 運用データの置き場（ADR-0016）。今のシェルの値を焼く
  sed -e "s#@@PYTHON@@#$py#g" -e "s#@@REPO@@#$REPO#g" -e "s#@@PATH@@#$path#g" -e "s#@@HOME@@#$HOME#g" -e "s#@@PORT@@#$PORT#g" -e "s#@@WORKSPACE@@#$ws#g" "$PLIST_SRC" > "$PLIST_DST"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
  log "launchd: $PLIST_DST を登録（python3=$py, port=${PORT}, workspace=${ws}）"
  local i; for i in $(seq 1 20); do
    curl -sf "http://127.0.0.1:$PORT/api/overview" >/dev/null 2>&1 && { log "ok: http://127.0.0.1:$PORT/  ログ: ~/Library/Logs/aifactory-console.log"; return 0; }
    sleep 0.5
  done
  echo "[install] error: ${PORT} で応答しない。~/Library/Logs/aifactory-console.log を見る" >&2; return 1
}

do_systemd() {
  command -v systemctl >/dev/null || { echo "[install] error: systemd が無い（macOS は --launchd）" >&2; exit 1; }
  local py path d sudo="" unit=/etc/systemd/system/aifactory-console.service host="${CONSOLE_HOST:-127.0.0.1}"
  [[ $EUID -eq 0 ]] || sudo="sudo"
  py="$(python3 -c 'import sys; print(sys.executable)')"
  "$py" -c 'import yaml, jsonschema' 2>/dev/null || echo "[install] warn: ${py} に yaml / jsonschema が無い。runner（kb run）がこの python3 で動かない" >&2
  path="$(dirname "$py"):$HOME/.local/bin"
  for c in claude gh jq sandbox ssh git; do
    d="$(command -v "$c" 2>/dev/null || true)"; [[ -n "$d" ]] && d="$(cd "$(dirname "$d")" && pwd -P)" && [[ ":$path:" != *":$d:"* ]] && path="$path:$d"
  done
  for d in /usr/local/bin /usr/bin /bin /usr/sbin /sbin; do [[ ":$path:" != *":$d:"* ]] && path="$path:$d"; done
  if [[ "$host" != 127.0.0.1 && "$host" != localhost && "$host" != ::1 ]]; then
    # 内側の網（10.x 等）なら合言葉は任意（ADR-0021）。0.0.0.0 やグローバルアドレスは console が起動を拒むので、ここで先に止める
    if ! python3 -c 'import ipaddress,sys; ip=ipaddress.ip_address(sys.argv[1]); sys.exit(0 if not ip.is_unspecified and not ip.is_global else 1)' "$host" 2>/dev/null; then
      grep -q '^CONSOLE_TOKEN=.\+' "$HOME/.config/aifactory/ctl.env" 2>/dev/null || { echo "[install] error: $host（内側の網ではない）に bind するには ~/.config/aifactory/ctl.env に CONSOLE_TOKEN=<合言葉> が要る" >&2; exit 1; }
    fi
  fi
  sed -e "s#@@PYTHON@@#$py#g" -e "s#@@REPO@@#$REPO#g" -e "s#@@USER@@#$(id -un)#g" -e "s#@@HOME@@#$HOME#g" -e "s#@@PATH@@#$path#g" -e "s#@@HOST@@#$host#g" -e "s#@@PORT@@#$PORT#g" \
    "$HERE/systemd/aifactory-console.service" | $sudo tee "$unit" >/dev/null
  $sudo systemctl daemon-reload; $sudo systemctl enable --now aifactory-console >/dev/null; $sudo systemctl restart aifactory-console
  log "systemd: aifactory-console（python3=$py, bind=${host}:${PORT}）"
  local i; for i in $(seq 1 20); do
    curl -sf -o /dev/null "http://$host:$PORT/api/overview" -H "Authorization: Bearer $(sed -n 's/^CONSOLE_TOKEN=//p' "$HOME/.config/aifactory/ctl.env" 2>/dev/null)" && { log "ok: http://$host:$PORT/  docs: http://$host:$PORT/docs/  ログ: journalctl -u aifactory-console"; return 0; }
    sleep 0.5
  done
  echo "[install] error: ${host}:${PORT} で応答しない。journalctl -u aifactory-console を見る" >&2; return 1
}

do_remove() {
  if command -v launchctl >/dev/null; then launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && log "launchd: $LABEL を外した" || log "launchd: 登録は無かった"; fi
  if command -v systemctl >/dev/null; then local sudo=""; [[ $EUID -eq 0 ]] || sudo="sudo"; $sudo systemctl disable --now aifactory-console 2>/dev/null && log "systemd: aifactory-console を外した" || true; $sudo rm -f /etc/systemd/system/aifactory-console.service; $sudo systemctl daemon-reload 2>/dev/null || true; fi
  rm -f "$PLIST_DST" "$BIN/aifactory-console"; log "removed: $PLIST_DST, $BIN/aifactory-console"
}

case "${1:-}" in
  "") do_link ;;
  --launchd) do_link; do_launchd ;;
  --systemd) do_link; do_systemd ;;
  --remove) do_remove ;;
  *) echo "usage: install.sh [--launchd|--systemd|--remove]" >&2; exit 1 ;;
esac
