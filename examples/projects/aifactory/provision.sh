#!/usr/bin/env bash
# examples/projects/aifactory/provision.sh: PJ テンプレート sb-tpl-aifactory の焼き込み（VM 内で dev として実行）。
#   aifactory = akkijp-oss/aifactory（公開）。Python 3 標準ライブラリ + pyyaml + jsonschema / bash / MkDocs Material
#   公開リポジトリなので clone にトークンは要らない。GH_TOKEN があれば gh の credential helper を登録する（無くても焼ける。push / PR は take 時に注入されるトークンで行う）
#   VM 内の :3000 はドキュメントサイト（mkdocs serve）。これが「画面」（sandbox url で開ける）
#   実行: ssh dev@10.77.0.116 "bash -s" < examples/projects/aifactory/provision.sh
set -euo pipefail
export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
export DEBIAN_FRONTEND=noninteractive

REPO="akkijp-oss/aifactory"
BRANCH="main"
APP_DIR="$HOME/app"
SU="$(command -v sudo)"
log() { echo; echo "[provision:aifactory] $*"; }

# ---------- 1. PJ 層の OS パッケージ: runner が使う yaml / jsonschema、website の venv 用
log "python packages"
$SU apt-get update -qq
$SU apt-get install -y -qq python3-yaml python3-jsonschema python3-venv python3-pip >/dev/null
python3 -c 'import yaml, jsonschema' && echo "yaml/jsonschema ok"

# ---------- 2. clone（公開リポジトリ。トークン不要）
log "clone $REPO ($BRANCH)"
if [[ -n "${GH_TOKEN:-}" ]]; then gh auth setup-git; fi
[ -d "$APP_DIR/.git" ] || git clone -q --branch "$BRANCH" "https://github.com/$REPO.git" "$APP_DIR"
cd "$APP_DIR"
git checkout -q "$BRANCH"; git pull -q --ff-only
git config --global -l | grep -q 'credential.https://github.com' || gh auth setup-git 2>/dev/null || true   # push 時は take で注入される GH_TOKEN を gh が使う

# ---------- 3. アプリ用 env
log "app env"
$SU mkdir -p /etc/sandbox
$SU tee /etc/sandbox/app.env >/dev/null <<EOT
# aifactory sandbox: aifactory 自身（テンプレート焼き込み時に生成。秘密情報は置かない）
SANDBOX_APP_DIR=$APP_DIR
EOT
$SU tee /etc/profile.d/sandbox-app.sh >/dev/null <<'EOT'
# aifactory sandbox: PJ テンプレートが用意したアプリ用 env
if [ -r /etc/sandbox/app.env ]; then set -a; . /etc/sandbox/app.env; set +a; fi
EOT
set -a; . /etc/sandbox/app.env; set +a

# ---------- 4. website の venv（MkDocs Material）
log "website venv"
python3 -m venv website/.venv
website/.venv/bin/pip install -q -r website/requirements.txt

# ---------- 5. 品質ゲート（gates.sh と同じ組）
log "quality gates"
: > "$HOME/GATES.txt"
gate() { local name=$1; shift; if "$@" > "$HOME/gate-$name.log" 2>&1; then echo "$name: PASS" | tee -a "$HOME/GATES.txt"; else echo "$name: FAIL (see ~/gate-$name.log)" | tee -a "$HOME/GATES.txt"; fi; }
gate unittest-workflow python3 -m unittest discover -s workflow/tests
gate unittest-console python3 -m unittest discover -s console/tests
gate mkdocs-strict website/.venv/bin/mkdocs build --strict -f website/mkdocs.yml
gate oss-check bin/oss-check.sh

# ---------- 6. systemd（:3000 = ドキュメントサイト）。clean スナップショットは起動済み状態で取る
log "systemd unit"
$SU tee /etc/systemd/system/sandbox-app.service >/dev/null <<EOT
[Unit]
Description=aifactory sandbox app ($REPO docs site, mkdocs serve)
After=network.target
[Service]
Type=simple
User=dev
WorkingDirectory=$APP_DIR
EnvironmentFile=/etc/sandbox/app.env
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$APP_DIR/website/.venv/bin/mkdocs serve -f $APP_DIR/website/mkdocs.yml -a 0.0.0.0:3000 --no-livereload
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOT
$SU systemctl daemon-reload
$SU systemctl enable --now sandbox-app >/dev/null
for i in $(seq 1 40); do curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/ 2>/dev/null | grep -qE '^[23][0-9]{2}$' && break; sleep 3; done
echo "app :3000 -> $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/)"

# ---------- 7. 後片付け
log "cleanup"
unset GH_TOKEN
gh auth logout --hostname github.com >/dev/null 2>&1 || true
rm -rf "$HOME/.config/gh/hosts.yml"
git config --global -l | grep -i token && { echo "[error] git config にトークンが残っている"; exit 3; } || true
rm -f "$HOME/.bash_history"
cat "$HOME/GATES.txt"
grep -q FAIL "$HOME/GATES.txt" && { echo "[provision:aifactory] ゲートに赤あり。ログを確認してから finalize するか判断する"; exit 2; }
echo "[ok] provision done. 次: TPL_VMID=<vmid> 32-pj-template.sh finalize aifactory"
