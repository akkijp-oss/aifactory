#!/usr/bin/env bash
# 31-provision-base.sh: ベーステンプレート VM の中で root として実行する（BUILD.md Step 3b）。
#   sudo bash /tmp/31-provision-base.sh
# 入れるもの:
#   OS      : TZ Asia/Tokyo, qemu-guest-agent, unattended-upgrades 無効（再現性優先）
#   build   : build-essential libpq-dev libyaml-dev libssl-dev zlib1g-dev libffi-dev libreadline-dev libvips imagemagick
#   tools   : git curl jq unzip ripgrep gh
#   db/kvs  : PostgreSQL 16 (role dev superuser, local trust), Redis
#   browser : Google Chrome stable (.deb。snap 回避)
#   lang    : mise (dev ユーザー), Node 22 LTS (mise global)。Ruby は PJ 層で .ruby-version に従って入れる
#   agent   : Claude Code (公式ネイティブインストーラ → ~/.local/bin/claude)
#   hooks   : /etc/tmpfiles.d/sandbox.conf (tmpfs /run/sandbox), /etc/profile.d/sandbox.sh (PATH + /run/sandbox/env 読み込み)
#   clean   : apt clean, cloud-init clean, machine-id 初期化, 履歴削除
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
DEV=dev
DEV_HOME=/home/$DEV

log() { echo "[provision] $*"; }

# ---------- OS
log "os basics"
timedatectl set-timezone Asia/Tokyo
apt-get update -qq
apt-get upgrade -y -qq >/dev/null
apt-get install -y -qq qemu-guest-agent ca-certificates gnupg curl wget git jq unzip ripgrep htop tmux \
  build-essential pkg-config autoconf bison libpq-dev libyaml-dev libssl-dev zlib1g-dev libffi-dev libreadline-dev \
  libgmp-dev libncurses-dev libgdbm-dev libdb-dev uuid-dev libvips imagemagick libjemalloc2 fonts-noto-cjk >/dev/null
systemctl enable --now qemu-guest-agent >/dev/null
systemctl disable --now unattended-upgrades >/dev/null 2>&1 || true
apt-get remove -y -qq unattended-upgrades >/dev/null 2>&1 || true

# ---------- gh
log "gh"
if ! command -v gh >/dev/null; then
  install -d -m 0755 /etc/apt/keyrings
  wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg > /etc/apt/keyrings/githubcli-archive-keyring.gpg
  chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list
  apt-get update -qq && apt-get install -y -qq gh >/dev/null
fi

# ---------- PostgreSQL / Redis
log "postgresql + redis"
apt-get install -y -qq postgresql postgresql-contrib redis-server >/dev/null
PG_VER="$(ls /etc/postgresql | sort -V | tail -1)"
PG_HBA="/etc/postgresql/$PG_VER/main/pg_hba.conf"
# dev 用: ローカルは trust（sandbox 内に閉じているため）
sed -i -E 's/^(local\s+all\s+all\s+)\S+/\1trust/; s/^(host\s+all\s+all\s+127\.0\.0\.1\/32\s+)\S+/\1trust/; s/^(host\s+all\s+all\s+::1\/128\s+)\S+/\1trust/' "$PG_HBA"
systemctl enable --now postgresql redis-server >/dev/null
systemctl restart postgresql
sudo -u postgres psql -tAc "select 1 from pg_roles where rolname='$DEV'" | grep -q 1 || sudo -u postgres createuser -s "$DEV"
# dev の既定 DB（psql の接続確認と、DB 名を指定しない PJ 用。無いと verify の psql が落ちる。2026-09-06 に踏んだ）
sudo -u postgres psql -tAc "select 1 from pg_database where datname='$DEV'" | grep -q 1 || sudo -u postgres createdb -O "$DEV" "$DEV"

# ---------- Chrome
log "google chrome"
if ! command -v google-chrome >/dev/null; then
  wget -qO /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  apt-get install -y -qq /tmp/chrome.deb >/dev/null
  rm -f /tmp/chrome.deb
fi

# ---------- sandbox hooks
log "sandbox hooks"
cat > /etc/tmpfiles.d/sandbox.conf <<EOF
d /run/sandbox 0750 $DEV $DEV -
EOF
systemd-tmpfiles --create /etc/tmpfiles.d/sandbox.conf
cat > /etc/profile.d/sandbox.sh <<'EOF'
# aifactory sandbox: mise shims と take 時に注入された env を読む（login shell で有効。CLI は bash -lc で入る）
export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
if [ -r /run/sandbox/env ]; then set -a; . /run/sandbox/env; set +a; fi
EOF
chmod 644 /etc/profile.d/sandbox.sh
echo "$DEV ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-$DEV && chmod 440 /etc/sudoers.d/90-$DEV

# ---------- mise + node + claude (dev ユーザー)
log "mise / node / claude code (as $DEV)"
sudo -iu "$DEV" bash <<'EOF'
set -euo pipefail
export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
command -v mise >/dev/null || curl -fsSL https://mise.run | sh >/dev/null
grep -q 'mise activate' ~/.bashrc || echo 'eval "$(~/.local/bin/mise activate bash)"' >> ~/.bashrc
mise settings set experimental true >/dev/null 2>&1 || true
mise use -g node@22 >/dev/null
mise reshim
node --version
command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash >/dev/null
claude --version
git config --global init.defaultBranch main
git config --global user.name "aifactory sandbox"
git config --global user.email "sandbox@aifactory.local"
EOF

# ---------- verify
log "verify"
sudo -iu "$DEV" bash -lc 'mise --version; node --version; claude --version; gh --version | head -1; psql -c "select 1" >/dev/null && echo psql-ok; redis-cli ping; google-chrome --version'
ls -ld /run/sandbox

# ---------- clean (焼く直前)
log "clean"
apt-get autoremove -y -qq >/dev/null; apt-get clean
cloud-init clean --logs || true
truncate -s0 /etc/machine-id
rm -f /var/lib/dbus/machine-id && ln -s /etc/machine-id /var/lib/dbus/machine-id
rm -f /root/.bash_history "$DEV_HOME/.bash_history"
journalctl --rotate >/dev/null 2>&1 || true; journalctl --vacuum-time=1s >/dev/null 2>&1 || true
log "done. 次: 30-base-template.sh finalize（ホスト側でシャットダウン → template 化）"
