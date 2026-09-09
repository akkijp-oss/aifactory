#!/usr/bin/env bash
# examples/projects/kumitate/prepare.sh: 貸出直後の準備（project.yml の prepare）。VM 内、checkout の直後に 1 回だけ実行される。
#   VM は run のたびにテンプレート（provision.sh 時点）へ戻るので、そこから develop が進んだ分だけ環境がずれる。
#   2026-09-09: provision 時点の migration で DB が固定され、develop に入った migration の列が無くて packages/db の
#   実 DB テストが全滅した（#288/#290/#293 で 3 回、実装役が「環境起因の赤」を直そうとして attempt を焼いた）。
#   ここで依存と DB を base に合わせてから agent を起こす。非 0 で終わると runner は agent を起動せず failure: prepare で止まる。
set -euo pipefail
export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
cd "${SANDBOX_APP_DIR:-$HOME/app/apps/kumitate}"
echo "[prepare:kumitate] pnpm install"
pnpm install --frozen-lockfile
echo "[prepare:kumitate] db:migrate"   # 加法 migration。冪等で数秒
pnpm --filter @kumitate/db db:migrate
