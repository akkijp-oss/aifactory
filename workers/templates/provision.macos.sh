#!/usr/bin/env bash
# workers/templates/provision.macos.sh: Mac worker の専用ゲスト用 provision.sh の雛形。
#   これは雛形。そのまま置いても動くが、PJ 固有の追加は「3. PJ 固有」の下に足す。
#   置き場: $AIFACTORY_WORKSPACE/projects/<pj>/provision.sh（PJ 定義と同じディレクトリ）
#   実行のされ方: runner が専用ゲストへ転送し、認証情報を注入する前に `bash provision.sh` で 1 回実行する。
#     clone は runner が行う。ここで clone しない・認証情報を触らない・何度実行しても同じ結果にする。
#   基準イメージに既に入っているものを入れ直すと壊れる。中身は docs/macos-worker.md の「基準イメージの中身」。
set -euo pipefail

# runner がゲストで組み立てるものと同じ PATH（workflow/lib/macos.py）。
# provision.sh はその PATH を継いで走るが、手で試すときも同じ見え方になるよう明示しておく。
export PATH="/opt/homebrew/opt/coreutils/libexec/gnubin:/opt/homebrew/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
export HOMEBREW_NO_AUTO_UPDATE=1
log() { echo; echo "[provision] $*"; }

# ---------- 1. ネットワーク隔離のプローブ
# 公開 HTTPS に出られること、private IPv4 / link-local / tailnet 宛ての TCP が落ちることを確かめる。
# 遮断そのものは Softnet が行う（範囲は workers/cmd/aifactory-worker/lifecycle.go の --net-softnet-block と同じ）。
# 到達しないことの確認は「宛先が存在しない」場合と区別できない。隔離が壊れていないかの最低限の見張りとして置く。
# どちらか外れていたら作業を始めずに落とす。
log "network isolation probe"
curl -fsS --max-time 20 -o /dev/null https://api.github.com/ || { echo "[error] 公開 HTTPS に到達できない"; exit 1; }
leaked=0
# 各レンジの先頭ホスト（x.y.z.0/n → x.y.z.1）へ TCP 22 を試す。宛先の値はここに書かず CIDR から作る。
for cidr in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16; do
  target="${cidr%/*}"; target="${target%.*}.1"
  if nc -z -G 3 -w 3 "$target" 22 2>/dev/null; then
    echo "[error] 遮断されているはずの $cidr（$target:22）へ到達した"
    leaked=1
  fi
done
[ "$leaked" -eq 0 ] || { echo "[error] ネットワーク隔離が効いていない"; exit 1; }
echo "isolation ok"

# ---------- 2. runner が使うツール（既存を壊さない）
# gh は上流の基準イメージに入っている。gh / coreutils / claude は専用基準VMの導入時にも入る。
# ここは「無ければ入れる」だけ。入っているものを brew で入れ直さない。
log "tools"
command -v gh      >/dev/null || brew install gh
command -v timeout >/dev/null || brew install coreutils
command -v claude  >/dev/null || curl -fsSL https://claude.ai/install.sh | bash

# ---------- 3. PJ 固有（ここから下に足す）
# 規則: brew formula は必ず `command -v X >/dev/null || brew install X` で守る。
#   npm -g / cask / curl で入っているものを brew の formula で入れ直さない（リンクが衝突して落ちる）。
#   keg-only のもの（node@24 など）は入れ直さず PATH を足す。
# 例:
#   command -v swiftlint >/dev/null || brew install swiftlint
#   if [ -d /opt/homebrew/opt/node@24/bin ]; then export PATH="/opt/homebrew/opt/node@24/bin:$PATH"; fi

# ---------- 4. 確認（何がどこにあるかをログに残す）
log "versions"
missing=0
for tool in git python3 brew gh timeout claude; do
  found="$(command -v "$tool" || true)"
  printf '%-8s %s\n' "$tool" "${found:-(見つからない)}"
  [ -n "$found" ] || missing=1
done
[ "$missing" -eq 0 ] || { echo "[error] runner が使うツールが揃っていない"; exit 1; }
echo "[ok] provision done"
