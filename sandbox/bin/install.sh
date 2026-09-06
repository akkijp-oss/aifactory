#!/usr/bin/env bash
# sandbox/bin/install.sh: sandbox CLI を ~/.local/bin に実体コピーする（BUILD.md Step 5b）。
#   シンボリックリンクだと launchd（gh-refresh）の bash が Documents 配下を読めず "Operation not permitted" になる（macOS の TCC）。
#   リポジトリの sandbox/bin/sandbox を更新したら、もう一度これを実行する
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HOME/.local/bin"
rm -f "$HOME/.local/bin/sandbox"
install -m 755 "$HERE/sandbox" "$HOME/.local/bin/sandbox"
echo "[ok] installed $HOME/.local/bin/sandbox ($(sed -n 2p "$HERE/sandbox" | cut -c1-40)...)"
