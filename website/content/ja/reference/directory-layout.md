# ディレクトリ構成

リポジトリには工場の**仕組み**だけが入ります。運用データ（PJ 定義、チケット、実行記録、ログ）は **workspace**（`AIFACTORY_WORKSPACE`、既定 `<repo>/workspace/`、git 追跡外）に置きます。

## リポジトリ

```
aifactory/
├── README.md  README.ja.md          # 全体像と「このリポジトリを読む AI へ」（英語 / 日本語）
├── LICENSE                          # Apache-2.0
├── CONTRIBUTING.md  SECURITY.md  CHANGELOG.md
├── .github/workflows/
│   ├── ci.yml                       # unittest（console/tests, workflow/tests）+ mkdocs build --strict
│   └── docs.yml                     # ドキュメントサイトのデプロイ（GitHub Pages）
├── examples/projects/<pj>/          # 同梱の PJ 定義の例（kumitate = akkijp/kumitate）
│   ├── provision.sh  project.yml  gates.sh
├── sandbox/                         # 区画1: どこで動くか
│   ├── README.md                    # 設計と契約
│   ├── BUILD.md                     # 構築手順書（コマンドと完了条件）
│   ├── STATUS.md                    # 進捗票と実機確認コマンド
│   ├── OPERATIONS.md                # 運用（貸出/返却/リセット/テンプレ更新/障害）
│   ├── bin/
│   │   ├── sandbox                  # Mac 側 CLI（bash）
│   │   ├── install.sh               # ~/.local/bin に実体コピー
│   │   └── gh-app-setup             # GitHub App を作る
│   ├── proxmox/                     # Proxmox 側スクリプト（番号順）
│   │   ├── run.sh                   # 入口（PVE_HOST に ssh して流す）
│   │   ├── 10-sdn.sh  20-gateway-lxc.sh  30-base-template.sh  31-provision-base.sh
│   │   └── 32-pj-template.sh  40-pool.sh  50-firewall.sh
│   └── templates/                   # 工場側の雛形だけ（PJ 固有は置かない）
│       ├── README.md                # provision.sh の書き方
│       ├── env.example  ssh_config.example  launchd/
│       └── base/README.md           # base 層に入っているもの
├── kanban/                          # 区画2: 何をいつやるか
│   ├── README.md
│   └── bin/kb                       # CLI（Python）。台帳は workspace/kanban/
├── workflow/                        # 区画3: どう進めるか
│   ├── README.md
│   ├── kit/                         # ★常備。全 PJ 共通。手順の形はここにしか無い
│   │   ├── schema/                  # workflow.schema.json / project.schema.json
│   │   ├── roles/                   # _common.md + planner / implementer / researcher / reviewer
│   │   ├── workflows/               # hotfix / bug / feature / chore / research / merge-pr
│   │   ├── steps/                   # gates.sh / pr-create.sh / pr-merge.sh
│   │   └── routes.env               # クラス → モデル
│   ├── bin/run                      # runner v1（Python）。記録は workspace/runs/
│   └── tests/                       # runner の unittest
├── glue/                            # 区画4: 区画をつなぐ
│   ├── README.md
│   ├── bin/intake                   # 自由文 → チケット（LLM 1 回）
│   └── bin/dispatch                 # todo → 実行。ログは workspace/logs/
├── console/                         # Web コンソールと MCP サーバー（Mac ローカル、Python 標準ライブラリ）
│   ├── lib/core.py                  # 読み書きの正本（console と mcp が共有）
│   ├── bin/console                  # HTTP サーバー + JSON API
│   ├── bin/mcp                      # MCP サーバー（stdio）
│   ├── static/                      # index.html / style.css / app.js（ビルド無し）
│   ├── tests/                       # unittest
│   └── jobs/<id>/                   # 起動した CLI の記録（git 追跡外。CONSOLE_JOBS で変更可）
├── .mcp.json                        # Claude Code 用の MCP 登録（console/bin/mcp）
├── docs/
│   ├── ledger.md                    # 構想台帳（現在地・決定・未決・履歴）
│   ├── adr/                         # 設計判断（1 判断 1 ファイル、追記のみ）
│   ├── sandbox-architecture.html    # sandbox の物理構成の解説
│   └── aifactory-how-it-works.html  # 仕組みの解説（1 枚 HTML）
└── website/                         # このドキュメントサイト（MkDocs）
    ├── mkdocs.yml  requirements.txt  README.md
    └── content/{ja,en}/
```

## workspace（`AIFACTORY_WORKSPACE`）

```
workspace/                           # 既定 <repo>/workspace/。git 追跡外
├── projects/<pj>/                   # ★PJ 固有はここだけ（examples/projects/ の例を写して作る）
│   ├── provision.sh                 # テンプレートの焼き込み
│   ├── project.yml                  # 事実と方針
│   └── gates.sh                     # 品質ゲート
├── kanban/
│   ├── kanban.db                    # 状態の正本（SQLite）
│   ├── tickets/<id>-<pj>-<slug>.md  # 本文の正本
│   └── BOARD.md                     # 生成物
├── runs/<日付>-<pj>-<id>/            # 実行記録
│   ├── ticket.md  state.json
│   ├── prompt-<step>-<n>.md  agent-<step>-<n>.log  code-<step>-<n>.log
│   ├── agent-<step>-<n>.jsonl       # 生イベント
│   └── work/                        # 回収した artifact
├── logs/
│   └── intake.log  dispatch.log
└── docs/                            # 自分の環境のメモ（ホスト、電源、トークンの期限など）
```

PJ 定義の探し方は `workspace/projects/<pj>/` → `examples/projects/<pj>/` の順です。`kb` / `intake` / `dispatch` / runner / コンソールのすべてがこの順で探します。

## リポジトリの外（秘密と貸出状態）

| パス | 内容 |
|---|---|
| `~/.config/sandbox/env` | CLI の設定（`PVE_HOST` / `GW_SSH` など） |
| `~/.config/sandbox/pj/<pj>.env` | PJ ごとのトークンと `GH_REPO` |
| `~/.config/sandbox/gh-app/` | GitHub App の ID と秘密鍵 |
| `~/.config/sandbox/state.json` | VM の貸出台帳 |
| `~/.ssh/conf.d/aifactory/` | VM 用の鍵と ssh 設定 |
| `~/.local/bin/sandbox` | CLI の実体コピー |
| `~/Library/LaunchAgents/com.aifactory.sandbox.gh-refresh.plist` | トークン更新の launchd |

## git 追跡の方針

| 追跡する | 追跡しない |
|---|---|
| 仕組み（`sandbox/` `kanban/` `workflow/` `glue/` `console/`） | `workspace/`（台帳、チケット、実行記録、ログ、PJ 定義） |
| `examples/projects/`（例） | `*.env`、`*.token`、`.env*` |
| `docs/`、`website/content/` | `website/site/`、`website/.venv/` |
| テスト（`console/tests/`、`workflow/tests/`） | `console/jobs/`、`__pycache__/` |
| | `*.img`、`*.qcow2`、`*.tar.zst` |
