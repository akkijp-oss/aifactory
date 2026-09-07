#!/usr/bin/env bash
# examples/projects/aifactory/gates.sh: aifactory 自身の品質ゲート（VM 内、$SANDBOX_APP_DIR で実行）。CI（.github/workflows/ci.yml）と同じ組 + 公開前チェック。
#   全部通れば 0、どれか赤なら非0。結果は標準出力に 1 行ずつ。ログは ~/gates/<name>.log
set -uo pipefail
cd "${SANDBOX_APP_DIR:-$HOME/app}"
mkdir -p "$HOME/gates"; rc=0
gate() { local name=$1; shift; if "$@" > "$HOME/gates/$name.log" 2>&1; then echo "PASS $name"; else echo "FAIL $name (~/gates/$name.log)"; rc=1; fi; }
bash_n() { local f; for f in sandbox/bin/sandbox sandbox/bin/install.sh sandbox/bin/gh-app-setup sandbox/proxmox/*.sh workflow/kit/steps/*.sh examples/projects/*/*.sh bin/*.sh console/bin/install.sh console/bin/mcp-remote; do bash -n "$f" || return 1; done; }
py_compile() { python3 -m py_compile lib/aifactory_paths.py workflow/bin/run kanban/bin/kb glue/bin/intake glue/bin/dispatch console/bin/console console/bin/mcp console/lib/core.py; }
paths_workspace() { python3 lib/aifactory_paths.py | tee /dev/stderr | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["legacy"] is False, d'; }
gate bash-n bash_n
gate py-compile py_compile
gate paths-workspace paths_workspace
gate unittest-workflow python3 -m unittest discover -s workflow/tests
gate unittest-console python3 -m unittest discover -s console/tests
gate mkdocs-strict website/.venv/bin/mkdocs build --strict -f website/mkdocs.yml
gate oss-check bin/oss-check.sh
exit $rc
