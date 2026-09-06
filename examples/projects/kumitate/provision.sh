#!/usr/bin/env bash
# examples/projects/kumitate/provision.sh: PJ テンプレート sb-tpl-kumitate の焼き込み（VM 内で dev として実行。GH_TOKEN を env で渡す）
#   kumitate = akkijp/kumitate（pnpm 10 monorepo + turbo / Node 22 / Next.js web :3000 / Postgres 16 + pgvector / drizzle / vitest）
#   リポジトリ直下は台帳・docs、アプリは apps/kumitate/（pnpm workspace: apps/{web,marketing,scheduler} packages/{db,dsl,generate,...}）
#   PR の宛先は develop。
#   実行: ssh dev@10.77.0.112 "GH_TOKEN=... bash -s" < sandbox/templates/kumitate/provision.sh
set -euo pipefail
export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
export DEBIAN_FRONTEND=noninteractive
: "${GH_TOKEN:?GH_TOKEN が必要（焼き込み時のみ。テンプレートには残さない）}"

REPO="akkijp/kumitate"
BRANCH="develop"
APP_ROOT="$HOME/app"
APP_DIR="$APP_ROOT/apps/kumitate"
PG_USER=kumitate; PG_PASS=kumitate; PG_DB=kumitate
SU="$(command -v sudo)"
log() { echo; echo "[provision:kumitate] $*"; }

# ---------- 1. PJ 層の OS パッケージ: pgvector（compose は pgvector/pgvector:pg16）
log "pgvector"
$SU apt-get update -qq
$SU apt-get install -y -qq postgresql-16-pgvector >/dev/null

# ---------- 2. clone（.git が約 870MB あるので時間がかかる）
log "clone $REPO ($BRANCH)"
gh auth setup-git
gh auth status >/dev/null
[ -d "$APP_ROOT/.git" ] || gh repo clone "$REPO" "$APP_ROOT" -- --branch "$BRANCH"
cd "$APP_ROOT"
git checkout "$BRANCH"; git pull --ff-only

# ---------- 3. Postgres: role + DB（base の PostgreSQL 16、ローカル trust）
log "postgres role/db"
psql -tAc "select 1 from pg_roles where rolname='$PG_USER'" | grep -q 1 || psql -c "create role $PG_USER login superuser password '$PG_PASS'"
for db in $PG_DB ${PG_DB}_test; do
  psql -tAc "select 1 from pg_database where datname='$db'" | grep -q 1 && psql -c "drop database $db"
  psql -c "create database $db owner $PG_USER"
  psql -d "$db" -c "create extension if not exists vector"
done

# ---------- 4. アプリ用 env（apps/kumitate/.env をリポジトリの雛形から生成。秘密は焼き込み時に乱数生成）
log "app env"
cd "$APP_DIR"
cp .env.example .env
sed -i -E "s#^DATABASE_URL=.*#DATABASE_URL=\"postgresql://$PG_USER:$PG_PASS@localhost:5432/$PG_DB\"#; s#^POSTGRES_PASSWORD=.*#POSTGRES_PASSWORD=\"$PG_PASS\"#; s#^SESSION_SECRET=.*#SESSION_SECRET=\"$(openssl rand -hex 32)\"#; s#^SECRET_ENCRYPTION_KEY=.*#SECRET_ENCRYPTION_KEY=\"$(openssl rand -hex 32)\"#; s#^NEXT_PUBLIC_APP_ORIGIN=.*#NEXT_PUBLIC_APP_ORIGIN=\"http://localhost:3000\"#" .env
$SU mkdir -p /etc/sandbox
$SU tee /etc/sandbox/app.env >/dev/null <<EOF
# aifactory sandbox: kumitate（テンプレート焼き込み時に生成。秘密情報は置かない。アプリの env は $APP_DIR/.env）
SANDBOX_APP_DIR=$APP_DIR
DATABASE_URL=postgresql://$PG_USER:$PG_PASS@localhost:5432/$PG_DB
EOF
$SU tee /etc/profile.d/sandbox-app.sh >/dev/null <<'EOF'
# aifactory sandbox: PJ テンプレートが用意したアプリ用 env
if [ -r /etc/sandbox/app.env ]; then set -a; . /etc/sandbox/app.env; set +a; fi
EOF
set -a; . /etc/sandbox/app.env; set +a

# ---------- 5. ランタイム: Node 22（base）+ pnpm（packageManager に固定）
log "pnpm via corepack"
PNPM_VER="$(node -p "require('./package.json').packageManager.split('@')[1]")"
corepack enable --install-directory "$HOME/.local/bin"
corepack prepare "pnpm@$PNPM_VER" --activate
pnpm --version
pnpm install --frozen-lockfile

# ---------- 6. DB migrate + seed（Makefile db-up と同じ）
log "db:migrate + db:seed"
pnpm --filter @kumitate/db db:migrate
pnpm --filter @kumitate/db db:seed

# ---------- 7. 品質ゲート（CI と同じ組）
log "quality gates"
: > "$HOME/GATES.txt"
gate() { local name=$1; shift; if "$@" > "$HOME/gate-$name.log" 2>&1; then echo "$name: PASS" | tee -a "$HOME/GATES.txt"; else echo "$name: FAIL (see ~/gate-$name.log)" | tee -a "$HOME/GATES.txt"; fi; }
gate tokens-check pnpm tokens:check
gate typecheck pnpm --filter @kumitate/dsl --filter @kumitate/db --filter @kumitate/generate --filter @kumitate/web --filter @kumitate/marketing typecheck
gate lint-web pnpm --filter @kumitate/web lint
gate lint-marketing pnpm --filter @kumitate/marketing lint
gate test pnpm --filter @kumitate/dsl --filter @kumitate/db --filter @kumitate/generate --filter @kumitate/web --filter @kumitate/marketing test
gate test-dom pnpm --filter @kumitate/web test:dom

# ---------- 8. systemd（web = next dev :3000）。clean スナップショットは起動済み状態で取る
log "systemd unit"
$SU tee /etc/systemd/system/sandbox-app.service >/dev/null <<EOF
[Unit]
Description=aifactory sandbox app ($REPO web, next dev)
After=network.target postgresql.service
Wants=postgresql.service
[Service]
Type=simple
User=dev
WorkingDirectory=$APP_DIR
EnvironmentFile=/etc/sandbox/app.env
Environment=PATH=$HOME/.local/bin:$HOME/.local/share/mise/shims:/usr/local/bin:/usr/bin:/bin
ExecStart=$HOME/.local/bin/pnpm --filter @kumitate/web dev
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF
$SU systemctl daemon-reload
$SU systemctl enable --now sandbox-app >/dev/null
# 2xx/3xx なら起動済み（next dev は初回コンパイルに時間がかかる）
for i in $(seq 1 80); do curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/ 2>/dev/null | grep -qE '^[23][0-9]{2}$' && break; sleep 3; done
echo "app :3000 -> $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/)"

# ---------- 9. 後片付け
log "cleanup"
unset GH_TOKEN
gh auth logout --hostname github.com >/dev/null 2>&1 || true
rm -rf "$HOME/.config/gh/hosts.yml"
git config --global -l | grep -i token && { echo "[error] git config にトークンが残っている"; exit 3; } || true
rm -f "$HOME/.bash_history"
cat "$HOME/GATES.txt"
grep -q FAIL "$HOME/GATES.txt" && { echo "[provision:kumitate] ゲートに赤あり。ログを確認してから finalize するか判断する"; exit 2; }
echo "[ok] provision done. 次: TPL_VMID=9112 32-pj-template.sh finalize kumitate"
