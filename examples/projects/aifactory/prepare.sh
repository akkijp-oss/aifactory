#!/usr/bin/env bash
# examples/projects/aifactory/prepare.sh: 貸出直後の準備（project.yml の prepare）。VM 内、checkout の直後に 1 回だけ実行される。
#   VM は run のたびにテンプレート（provision.sh 時点）へ戻るので、テンプレートに焼いた Go は固定で、
#   base の workers/go.mod だけが進む。ここで「焼いた Go が今の go.mod の要求を満たすか」を機械で確かめる。
#   足りなければ同じ手順で入れ直し、それでも満たせなければ非 0 で終わる（runner は agent を起動せず failure: prepare）。
#   古い Go のまま go test を通さないのが眼目（ADR-0071、チケット 488）。入れ直しが走ったらテンプレートの焼き直し時期。
set -euo pipefail
export PATH="/usr/local/go/bin:$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
cd "${SANDBOX_APP_DIR:-$HOME/app}"

# PJ 定義は checkout と別に配られる（配布は制御系の作業ツリー）。この script が無い版を checkout した run では黙って通す
if [ ! -f bin/go-toolchain.sh ]; then
  echo "[prepare:aifactory] bin/go-toolchain.sh が無い checkout。go の確認はしない"
  exit 0
fi

echo "[prepare:aifactory] go toolchain"
if ! bash bin/go-toolchain.sh check; then
  echo "[prepare:aifactory] テンプレート（sb-tpl-aifactory）の Go が workers/go.mod の要求に足りない。入れ直す。" \
       "恒久対応は provision.sh でテンプレートを焼き直すこと"
  # ★入れ直せなかったときに非 0 で終わらない（チケット 488 の PM レビュー）。この script は **全 run** が通るので、
  #   網・ミラー・DNS の不調で Go を取れなかっただけで failure: prepare にすると、Go を 1 行も触らない票
  #   （文書 / console / kanban）まで道連れで止まる。Go が要る run は後続の工程で `go` が無くて落ちるので、
  #   そこで気づける。「取得できなかった」と「取得したが版が足りない」を別扱いにするのが眼目。
  if ! bash bin/go-toolchain.sh ensure; then
    echo "[prepare:aifactory] Go を用意できなかった（網かミラー）。workers/ を触る run はこの後の工程で落ちる。" \
         "他の run は続行する" >&2
    exit 0
  fi
fi
# go が無い回（上で握った）はここも飛ばす。go mod download だけのために prepare を落とさない
if command -v go >/dev/null 2>&1; then
  (cd workers && go mod download) || echo "[prepare:aifactory] go mod download が失敗（網）。続行する" >&2
fi
