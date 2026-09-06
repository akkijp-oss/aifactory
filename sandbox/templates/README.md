# templates: テンプレートの焼き込み

2層構成。base は全 PJ 共通（`../proxmox/31-provision-base.sh`）、PJ 層は PJ 定義ディレクトリの `provision.sh` に書く。

```
sandbox/templates/                 # 枠組み（公開物。環境固有の値は書かない）
├── README.md                      # このファイル
├── env.example                    # ~/.config/sandbox/env の雛形
├── ssh_config.example             # ~/.ssh/conf.d/aifactory/config の雛形
├── launchd/                       # GitHub App トークン更新の launchd plist
└── base/README.md                 # base 層に入っているものの一覧（31-provision-base.sh の要約）

$AIFACTORY_WORKSPACE/projects/<pj>/   # PJ 定義（私有。既定は <repo>/workspace/projects/。ADR-0016）
├── project.yml                    # 事実と方針（リポジトリ・base ブランチ・app_dir・stack・review_points・forbidden）。schema は workflow/kit/schema/project.schema.json
├── provision.sh                   # PJ 層の焼き込み。VM 内で dev として実行（GH_TOKEN を env で渡す）
└── gates.sh                       # 品質ゲート。VM 内 $SANDBOX_APP_DIR で実行。全部通れば 0

examples/projects/<pj>/            # 同梱サンプル。kumitate = akkijp/kumitate（pnpm / Next.js monorepo）
```

PJ 定義の探索順は `$AIFACTORY_WORKSPACE/projects/<pj>` → `examples/projects/<pj>`。新しい PJ は `examples/projects/kumitate/` を workspace 側へ写して直すのが早い。

## PJ 層 `provision.sh` の書き方

VM（`sb-tpl-{pj}`、10.77.0.(VMID−9000)。9110 なら 10.77.0.110）の中で `dev` ユーザーとして実行される。`GH_TOKEN` は環境変数で渡される。Mac からは:

```bash
GH_TOKEN=$(gh auth token) ssh -i ~/.ssh/conf.d/aifactory/sb_ed25519 dev@10.77.0.110 "GH_TOKEN=$GH_TOKEN bash -s" < "$AIFACTORY_WORKSPACE/projects/{pj}/provision.sh"
```

雛形（Rails + PostgreSQL の場合）:

```bash
#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
REPO="org/repo"            # 対象リポジトリ
APP="$HOME/app"

# 1. clone（トークンは URL に埋めず gh 経由。GH_TOKEN が環境変数にあれば gh はそれを使う。`gh auth login` は不要=呼ぶと拒否される）
gh auth setup-git           # git の credential helper に gh を登録（take 時に注入される GH_TOKEN で push できる）
gh repo clone "$REPO" "$APP"
cd "$APP"

# 2. ランタイム（.ruby-version / .node-version に従う）
mise install
mise reshim

# 3. 依存
bundle install --jobs 4
[ -f package-lock.json ] && npm ci || true

# 4. DB（PostgreSQL は base で role dev / trust 済み）
cp -n config/database.yml.example config/database.yml 2>/dev/null || true
bin/rails db:create db:schema:load db:seed

# 5. アプリを systemd で常駐（clean スナップショットは起動済み状態で取る）
sudo tee /etc/systemd/system/sandbox-app.service >/dev/null <<EOT
[Unit]
Description=aifactory sandbox app ($REPO)
After=network.target postgresql.service redis-server.service
[Service]
Type=simple
User=dev
WorkingDirectory=$APP
Environment=PATH=/home/dev/.local/bin:/home/dev/.local/share/mise/shims:/usr/local/bin:/usr/bin:/bin
Environment=RAILS_ENV=development
ExecStart=/home/dev/.local/share/mise/shims/bundle exec rails server -b 0.0.0.0 -p 3000
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOT
sudo systemctl daemon-reload
sudo systemctl enable --now sandbox-app

# 6. 品質ゲートが緑であることを確認してから焼く
bin/rubocop
bin/rails test

# 7. 焼く前の後片付け（必ず）
unset GH_TOKEN
gh auth logout --hostname github.com || true
rm -f ~/.config/gh/hosts.yml   # credential helper の登録（~/.gitconfig）は残してよい。トークンは残さない
rm -f ~/.bash_history
echo "[ok] provision done. 次: 32-pj-template.sh finalize {pj}"
```

PJ ごとに変えるのは `REPO`、依存コマンド、DB 準備、起動コマンド、品質ゲート。それ以外は雛形のまま。

Rails + MySQL の PJ で雛形に足した点（実績）。同種の PJ はこれを写すほうが早い:
- リポジトリ直下が infra でアプリがサブディレクトリ（`~/app/<app>`）。`SANDBOX_APP_DIR` を `/etc/sandbox/app.env` に書く（`project.yml` の `app_dir` と揃える）
- MySQL 8 を PJ 層で apt install（base は PostgreSQL のみ）。root は `password`
- アプリ用 env は `/etc/sandbox/app.env` に置き、systemd（`EnvironmentFile`）と login shell（`/etc/profile.d/sandbox-app.sh`）の両方で読む。`RAILS_DEVELOPMENT_HOSTS=.sb.internal` で `task-*.sb.internal` を許可
- web（`sandbox-app`）と sidekiq（`sandbox-worker`）の2ユニット
- 品質ゲートは CI と同じ組（rubocop / rspec / minitest / brakeman）。結果を `~/GATES.txt` に残し、赤があれば非0で終わる

Node monorepo の PJ は `examples/projects/kumitate/provision.sh` が実例（corepack で pnpm を固定、`pnpm install --frozen-lockfile`、PostgreSQL に PJ 用 role / DB、drizzle migrate、`pnpm dev` を systemd で常駐）。

## `gates.sh` の書き方

VM 内で `$SANDBOX_APP_DIR`（無ければ `~/app`）に cd し、CI と同じ組を 1 つずつ `PASS name` / `FAIL name (~/gates/name.log)` の行で出す。全部通れば 0、どれか赤なら非0。workflow runner はこの終了コードで合否を決め、赤の行だけを agent に戻す。実例は `examples/projects/kumitate/gates.sh`。

## 焼く前の後片付け（base / PJ 共通）
- 認証情報を残さない: `gh auth logout`、`~/.config/gh` 削除、`GH_TOKEN` は環境変数のみ
- 履歴を消す: `~/.bash_history`
- base 層ではさらに `cloud-init clean --logs` と machine-id の初期化（31 が実施）

## PJ 層で焼いてよいもの / だめなもの
| 焼いてよい | 焼いてはいけない |
|---|---|
| リポジトリの clone（base ブランチ）、依存、seed 済み DB、systemd ユニット | GitHub トークン、Claude のトークン、本番の secrets、`.env` の実値 |
| Mac の SSH 公開鍵（cloud-init 由来） | SSH 秘密鍵 |

## 焼き込みで踏んだ罠（複数 PJ の実績から）

| 罠 | 症状 | 対処 |
|---|---|---|
| `GH_TOKEN` を環境変数で渡したまま `gh auth login --with-token` | gh が「環境変数を使う」と言って拒否し、後続の clone が止まる | `gh auth login` を呼ばない。`gh auth setup-git` だけ行い、gh は環境変数のトークンをそのまま使う |
| `/etc/sandbox/app.env` に `RAILS_ENV=development` | login shell で走らせた rspec / minitest が test でなく development 環境で動き、大量に落ちる（`Rails.env が test である` の spec が赤） | `RAILS_ENV` は systemd ユニットの `Environment=` にだけ書く。ゲートは `env RAILS_ENV=test` を明示 |
| Rails PJ で、開発用の DB ロール名（読み取り専用ロールなど）を app.env に書く | test 環境がそのロールで接続しに行き、test DB に入れず全滅 | development の既定値と同じなら書かない |
| AWS SDK を使う PJ が EC2 メタデータ（169.254.169.254）を引く | S3 を触るたびに数秒待たされる | `AWS_EC2_METADATA_DISABLED=true` を app.env に入れる |
| PJ の `db/seeds.rb` が base ブランチで壊れている | モデルの必須項目追加に seed が追随せず `RecordInvalid` | seed しない。リポジトリ公式の開発用エントリポイントと同じく schema + テストユーザーだけ作る。**リポジトリ側へ報告する価値あり** |
| 焼き直しで `sudo mysql` が使えない | root を password 認証に切り替えた後は auth_socket が効かない | `mysql -uroot -p… -e "select 1"` が通れば ALTER をスキップ（冪等化） |
| `sudo npx playwright install-deps` を root で実行 | root が dev の mise 設定を読み「config not trusted」で落ちる | dev のまま `npx playwright install-deps`（内部で sudo apt-get を呼ぶ） |
| pnpm の版 | `packageManager` に固定されている | `corepack enable --install-directory ~/.local/bin` + `corepack prepare pnpm@<版> --activate` |
| kicad-cli | PPA に単体パッケージが無い | `kicad` 本体（約 1GB）にフォールバック。入れると CI で一度も走っていなかったオラクルテストが動き、赤が見える（kicad を使う PJ で 6 件） |
| Playwright の visual-regression | 基準画像が `*-darwin.png` しか無く Linux 初回は必ず赤。書き出された `*-linux.png` が未追跡で残る | 情報扱いにし、`git clean -fd -- apps/*/tests` で clone を clean に戻す |
| 時間依存テスト（kumitate calendar 2 件） | フィクスチャが 8 月固定で、今月を描画するカレンダーが月替わりで落ちる。Mac の develop でも同じ | sandbox では直さない（リポジトリ側）。ゲートの合否判定は他のテストで見る |

## ゲートの扱い

- **gate**（焼き込みの合否に使う）: リポジトリの CI が守っているもの。赤なら焼かない
- **info**（記録するが合否に使わない）: リポジトリ側で守られていない・環境依存で必ず赤になるもの（例: 大量の rubocop 違反を抱えた PJ の rubocop / brakeman、依存の脆弱性で赤になる audit ゲート、基準画像が OS 依存の e2e、kumitate の時間依存 2 件）。`~/GATES.txt` に `RED (info)` と残す
- どちらに入れるかは各 `provision.sh` / `gates.sh` に理由つきで書く。上に載る workflow は gate の組だけを失敗判定に使う（base で既に赤いゲートは `project.yml` の `known_red_gates` に書き、agent に戻さない）
