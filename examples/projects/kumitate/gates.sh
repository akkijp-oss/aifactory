#!/usr/bin/env bash
# examples/projects/kumitate/gates.sh: kumitate の品質ゲート（VM 内、$SANDBOX_APP_DIR で実行）。CI（ci.yml）と同じ組。
#   全部通れば 0、どれか赤なら非0。結果は標準出力に 1 行ずつ。ログは ~/gates/<name>.log
set -uo pipefail
export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
cd "${SANDBOX_APP_DIR:-$HOME/app/apps/kumitate}"
mkdir -p "$HOME/gates"; rc=0
gate() { local name=$1; shift; if "$@" > "$HOME/gates/$name.log" 2>&1; then echo "PASS $name"; else echo "FAIL $name (~/gates/$name.log)"; rc=1; fi; }
gate tokens-check pnpm tokens:check
gate typecheck pnpm --filter @kumitate/dsl --filter @kumitate/db --filter @kumitate/generate --filter @kumitate/web --filter @kumitate/marketing typecheck
gate lint-web pnpm --filter @kumitate/web lint
gate lint-marketing pnpm --filter @kumitate/marketing lint
gate test pnpm --filter @kumitate/dsl --filter @kumitate/db --filter @kumitate/generate --filter @kumitate/web --filter @kumitate/marketing test
gate test-dom pnpm --filter @kumitate/web test:dom
exit $rc
