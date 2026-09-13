#!/usr/bin/env bash
# bin/go-toolchain.sh: workers/（Go）を触るための Go ツールチェーンを用意する / 確かめる。
#   要求する版の正本は workers/go.mod の `go` 行（`toolchain` 行があればそちら）だけ。ここにも provision にも版を書かない
#   （書くと go.mod を上げたときに黙ってずれる）。CI も actions/setup-go の `go-version-file: workers/go.mod` で同じ行を見ている。
#
#   bin/go-toolchain.sh required          要求する版を 1 行で出す
#   bin/go-toolchain.sh url               その版の公式 tarball の URL（この OS / CPU 向け）
#   bin/go-toolchain.sh check             PATH の go が要求を満たすか。満たさなければ非 0 で理由を出す
#   bin/go-toolchain.sh install [prefix]  公式 tarball を <prefix>/go（既定 /usr/local/go）へ展開し PATH を通す
#   bin/go-toolchain.sh ensure  [prefix]  check して、満たしていなければ install してもう一度 check
#
# なぜ apt の golang-go ではないか: Ubuntu 24.04 は 1.22 で、workers/go.mod の要求に足りない（チケット 488）。
# 入れる先を /usr/local/go にし PATH は /etc/profile.d/go.sh で通す（sandbox ssh は bash -lc なので login shell で読まれる）。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GO_MOD="${GO_MOD:-$ROOT/workers/go.mod}"
SU="$(command -v sudo || true)"; if [ "$(id -u)" = 0 ]; then SU=""; fi
say() { echo "[go-toolchain] $*"; }
die() { echo "[go-toolchain] $*" >&2; exit 1; }

required() {
  [ -r "$GO_MOD" ] || die "go.mod が読めない: $GO_MOD"
  local v
  # toolchain 行があれば実際に使われるのはそちら（go1.2.3 表記）。無ければ go 行
  v="$(awk '$1=="toolchain"{sub(/^go/,"",$2); print $2; exit}' "$GO_MOD")"
  [ -n "$v" ] || v="$(awk '$1=="go"{print $2; exit}' "$GO_MOD")"
  [ -n "$v" ] || die "$GO_MOD に go 行が無い"
  printf '%s\n' "$v"
}

# $1 >= $2 か（x.y.z を数値で。rc1 のような接尾辞は無視して読む）
ver_ge() {
  awk -v a="$1" -v b="$2" 'BEGIN{
    na=split(a,x,"."); nb=split(b,y,".");
    for(i=1;i<=3;i++){ ai=(i<=na)?int(x[i]):0; bi=(i<=nb)?int(y[i]):0;
      if(ai>bi) exit 0; if(ai<bi) exit 1 }
    exit 0}'
}

# 配布物の名前。go1.21 以降は初版も go1.N.0 という名前で配られるので、go.mod が `go 1.26` なら go1.26.0 を取る
release_name() {
  local v="$1"; case "$v" in *.*.*) ;; *) v="$v.0" ;; esac; printf 'go%s\n' "$v"
}

url() {
  local os arch
  case "$(uname -s)" in Linux) os=linux ;; Darwin) os=darwin ;; *) die "未対応の OS: $(uname -s)" ;; esac
  case "$(uname -m)" in x86_64|amd64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; *) die "未対応の CPU: $(uname -m)" ;; esac
  printf 'https://go.dev/dl/%s.%s-%s.tar.gz\n' "$(release_name "$(required)")" "$os" "$arch"
}

check() {
  local req have
  req="$(required)"
  command -v go >/dev/null 2>&1 || { echo "[go-toolchain] go が PATH に無い（workers/go.mod の要求は $req）" >&2; return 1; }
  have="$(go version 2>/dev/null | awk '{print $3}')"; have="${have#go}"
  [ -n "$have" ] || { echo "[go-toolchain] go version が読めない: $(command -v go)" >&2; return 1; }
  ver_ge "$have" "$req" || { echo "[go-toolchain] go$have は workers/go.mod の要求 $req に足りない（$(command -v go)）" >&2; return 1; }
  say "go$have >= $req ($(command -v go))"
}

install_go() {
  local prefix="${1:-/usr/local}" req tgz tmp
  req="$(required)"; tgz="$(url)"
  say "install $(release_name "$req") -> $prefix/go"
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/go.tar.gz" "$tgz" || { rm -rf "$tmp"; die "取得できない: $tgz"; }
  $SU rm -rf "$prefix/go"
  $SU mkdir -p "$prefix"
  $SU tar -C "$prefix" -xzf "$tmp/go.tar.gz" || { rm -rf "$tmp"; die "展開できない: $tmp/go.tar.gz"; }
  rm -rf "$tmp"
  # 既定 PATH にある /usr/local/bin から張る。login shell でない場合（agent が開く素の bash）でも `go` が見つかる
  if [ -d "$prefix/bin" ] || $SU mkdir -p "$prefix/bin"; then
    $SU ln -sfn "$prefix/go/bin/go" "$prefix/bin/go"
    $SU ln -sfn "$prefix/go/bin/gofmt" "$prefix/bin/gofmt"
  fi
  if [ -d /etc/profile.d ]; then   # login shell では GOPATH の bin も通す（go install で入れた道具用）
    $SU tee /etc/profile.d/go.sh >/dev/null <<EOT
# aifactory: Go ツールチェーン（版は workers/go.mod。bin/go-toolchain.sh が入れた）
export PATH="$prefix/go/bin:\$HOME/go/bin:\$PATH"
EOT
    $SU chmod 644 /etc/profile.d/go.sh
  fi
  export PATH="$prefix/go/bin:$PATH"
}

ensure() {
  local prefix="${1:-/usr/local}"
  export PATH="$prefix/go/bin:$PATH"
  check && return 0
  install_go "$prefix"
  check || die "入れ直しても要求 $(required) を満たせない"
}

case "${1:-}" in
  required) required ;;
  url)      url ;;
  check)    check ;;
  install)  shift; install_go "${1:-/usr/local}" ;;
  ensure)   shift; ensure "${1:-/usr/local}" ;;
  *) die "使い方: bin/go-toolchain.sh required|url|check|install [prefix]|ensure [prefix]" ;;
esac
