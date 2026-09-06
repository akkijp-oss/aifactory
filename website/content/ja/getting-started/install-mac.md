# Mac 側のセットアップ

このページで分かること: リポジトリを clone してから、Mac 側の 5 つの CLI が動くまで。sandbox の実機がまだ無くても、ここまでは進められます。

## 1. リポジトリを clone する

```bash
git clone git@github.com:akkijp-oss/aifactory.git
cd aifactory
```

Python の依存は runner と intake が使う 2 つだけです。

```bash
python3 -m pip install --user pyyaml jsonschema
python3 -c "import yaml, jsonschema; print('ok')"
```

## 2. 鍵と設定ファイルを置く 🤖

VM とゲートウェイに入るための鍵、CLI の設定、ssh の設定を作ります。秘密情報はすべて **リポジトリの外**（`~/.config/sandbox/` と `~/.ssh/conf.d/aifactory/`）に置きます。

```bash
mkdir -p ~/.ssh/conf.d/aifactory ~/.config/sandbox
[ -f ~/.ssh/conf.d/aifactory/sb_ed25519 ] || ssh-keygen -t ed25519 -N "" -C "aifactory-sandbox" -f ~/.ssh/conf.d/aifactory/sb_ed25519
grep -q "conf.d/aifactory" ~/.ssh/config || echo "Include ~/.ssh/conf.d/aifactory/config" >> ~/.ssh/config
cp sandbox/templates/ssh_config.example ~/.ssh/conf.d/aifactory/config
cp sandbox/templates/env.example ~/.config/sandbox/env && chmod 600 ~/.config/sandbox/env
```

`~/.config/sandbox/env` の中身を環境に合わせます。`PVE_HOST` と `GW_SSH` には既定値が無く、書かないと `sandbox` はエラーで止まります。

| 変数 | 意味 | 既定 |
|---|---|---|
| `PVE_HOST` | Proxmox ホストの ssh エイリアス（例: `pve1`） | 無し（必須） |
| `GW_SSH` | ゲートウェイ LXC への ssh 先（例: `root@10.77.0.2`） | 無し（必須） |
| `SB_KEY` | VM / LXC 用の秘密鍵 | `~/.ssh/conf.d/aifactory/sb_ed25519` |
| `SB_DOMAIN` | VM の DNS ドメイン | `sb.internal` |
| `APP_PORT` | アプリのポート | `3000` |
| `SB_JUMP` | 空なら tailnet 直結。Tailscale が使えない間は `PVE_HOST` と同じ値を入れると Proxmox ホスト経由（ProxyJump）で入る | 空 |
| `SB_POOL_NET` | プール VM が並ぶ /24 のプレフィックス。Proxmox 側の `SB_NET.1` とそろえる | `10.77.1` |
| `SB_POOL_BASE` | プール VM の VMID の起点。Proxmox 側の `SB_POOL_BASE` とそろえる | `9200` |

トークン（`CLAUDE_CODE_OAUTH_TOKEN` / `GH_TOKEN`）はこのファイルには書かず、次の節で PJ ごとに保存します。

### 作業データの置き場（workspace）

PJ 定義、チケット、実行記録、ログはリポジトリの外の **workspace** に置きます。既定は `<repo>/workspace/`（git 追跡外）で、環境変数 `AIFACTORY_WORKSPACE` で別の場所にできます。

```
$AIFACTORY_WORKSPACE/
├── projects/<pj>/     # project.yml / provision.sh / gates.sh（PJ 定義）
├── kanban/            # kanban.db / tickets/ / BOARD.md
├── runs/<run>/        # 実行記録
├── logs/              # intake.log / dispatch.log
└── docs/              # 自分用のメモ
```

PJ 定義は `workspace/projects/<pj>/` → `examples/projects/<pj>/` の順に探すので、同梱の例（`kumitate`）はコピーしなくてもそのまま使えます。自分の PJ は例を写して作ります（[PJ を追加する](../guides/add-project.md)）。

## 3. sandbox CLI を PATH に入れる 🤖

```bash
sandbox/bin/install.sh        # ~/.local/bin/sandbox に実体コピー
sandbox ls                    # 設定が読めれば貸出状況（空でもよい）が出る
```

!!! note "シンボリックリンクではなくコピーにする理由"
    launchd（GitHub トークンの定期更新）の bash は macOS の TCC で `Documents/` 配下を読めません。リンクだと "Operation not permitted" になるので、`install.sh` は実体をコピーします。**リポジトリの `sandbox/bin/sandbox` を更新したら、もう一度 `install.sh` を実行**してください。

kanban / glue / workflow の CLI はリポジトリ内のパスで直接呼びます（PATH に入れる必要はありません）。

```bash
kanban/bin/kb list
glue/bin/intake --help
glue/bin/dispatch --help
workflow/bin/run
```

## 4. Claude Code のトークンを PJ ごとに保存する 🧑

VM の中で Claude Code を動かすには、PJ ごとに長期トークンを発行して保存します。

```bash
claude setup-token                 # ブラウザで認証 → トークンが表示される
sandbox token set kumitate         # 対話で貼り付け → ~/.config/sandbox/pj/kumitate.env に保存
sandbox token show kumitate        # マスク表示で確認
```

PJ 別ファイル（`~/.config/sandbox/pj/<pj>.env`）には、GitHub App がトークンを限定するための `GH_REPO=owner/name` も書きます。

```bash
cat ~/.config/sandbox/pj/kumitate.env
# GH_REPO=akkijp/kumitate
# CLAUDE_CODE_OAUTH_TOKEN=...
```

なぜ PJ ごとか、なぜ OAuth の焼き込みではないかは [安全と秘密情報](../concepts/security.md) と ADR-0005 / 0006。

## 5. GitHub App を作って install する 🤖 → 🧑

VM から push / PR / マージするための権限は、静的トークンではなく **GitHub App の 1 時間トークン**で渡します（ADR-0008）。

```bash
sandbox/bin/gh-app-setup            # ブラウザが開く → 🧑 Create GitHub App を押す → ~/.config/sandbox/gh-app/ に保存
sandbox gh-app status               # 🧑 表示された install リンクで各オーナーに install する
```

`status` が全 PJ で `OK` になれば完了です。App に必要な権限は Contents（write）、Pull requests（write）、Metadata（read）、Actions（read）、Workflows（write。無いと `.github/workflows/` を触る push を GitHub が拒否します）。Checks（read）は任意です。

トークンは 1 時間で切れるので、45 分ごとに更新する launchd を登録します。

```bash
cp sandbox/templates/launchd/com.aifactory.sandbox.gh-refresh.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.aifactory.sandbox.gh-refresh.plist
launchctl list | grep aifactory
```

## 6. 確認

```bash
ls -l ~/.ssh/conf.d/aifactory/ ~/.config/sandbox/ ~/.config/sandbox/pj/
sandbox gh-app status
sandbox token show
```

sandbox の実機がある場合は、さらに次で疎通を見ます。

```bash
ping -c1 -W1 10.77.0.2 && echo "tailnet → sb-gw OK"
dig +short sb-gw.sb.internal
sandbox ls
```

sb-gw に届かなければ [sandbox の構築](build-sandbox.md) の Step 2、または [トラブルシューティング](../troubleshooting.md) へ。

## 7. 別の Mac に同じ形を作る

手元を「利用者と同じ形」に保つための約束です。clone は 2 本、秘密情報は同期しません。

```
~/Documents/GitHub/
├── <org>/aifactory              # 公開の枠組み（この リポジトリの clone）
└── <you>/aifactory-workspace    # 運用データ（private リポジトリ。PJ 定義・チケット・実行記録・私有メモ）
```

1. 2 本を clone する。workspace 側が無ければ `mkdir -p projects kanban runs logs docs` の空ディレクトリで始めてよい
2. 置き場を教える: `echo ~/Documents/GitHub/<you>/aifactory-workspace > ~/.config/aifactory/workspace`（シェルに `export AIFACTORY_WORKSPACE=…` でも可。両方あれば環境変数が優先）
3. 枠組み側で `pip install pyyaml jsonschema`、`bin/install-hooks.sh`（gitleaks を PATH に）、`console/bin/install.sh --launchd`、`sandbox/bin/install.sh`
4. 秘密情報は手で入れる: `~/.config/sandbox/env`（`env.example` から）、`~/.config/sandbox/pj/<pj>.env`（`sandbox token set`）、GitHub App の `~/.config/sandbox/gh-app/`、ssh 鍵 `~/.ssh/conf.d/aifactory/`。これらはどのリポジトリにも入れない
5. `kanban/bin/kb list` と http://127.0.0.1:8765/ が workspace の中身を出せば完了

workspace は私有の git リポジトリなので、チケットや実行記録が増えたら workspace 側でもコミットして push する（枠組み側の `git status` には出ない）。
