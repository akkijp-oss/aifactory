# base 層（sb-base, VMID `SB_BASE_VMID`。既定 9100）に入っているもの

正本は `../../proxmox/31-provision-base.sh`。ここは一覧のみ。

| 分類 | 内容 |
|---|---|
| OS | Ubuntu 24.04 cloud image、TZ Asia/Tokyo、qemu-guest-agent、unattended-upgrades 削除 |
| ユーザー | `dev`（sudo NOPASSWD、手元の Mac の公開鍵） |
| ビルド依存 | build-essential、libpq-dev、libyaml-dev、libssl-dev、zlib1g-dev、libffi-dev、libreadline-dev、libgmp-dev、libvips、imagemagick、libjemalloc2、fonts-noto-cjk |
| ツール | git、curl、wget、jq、unzip、ripgrep、htop、tmux、gh |
| DB / KVS | PostgreSQL（Ubuntu 24.04 同梱の 16）role `dev` superuser、ローカル trust。Redis |
| ブラウザ | Google Chrome stable（.deb） |
| 言語 | mise（dev ユーザー、`~/.local/bin/mise`、shims は `~/.local/share/mise/shims`）、Node 22（mise global） |
| エージェント | Claude Code（`~/.local/bin/claude`） |
| フック | `/run/sandbox`（tmpfs、dev 所有）、`/etc/profile.d/sandbox.sh`（PATH と `/run/sandbox/env` の読み込み） |
| 後片付け | apt clean、cloud-init clean、machine-id 初期化、履歴削除 |

Ruby は入っていない（PJ 層で `.ruby-version` に従って `mise install`）。MySQL も入っていない（必要な PJ は PJ 層で apt install。`../README.md`）。
