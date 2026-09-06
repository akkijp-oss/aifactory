# 設定の出どころ

このページで分かること: どの設定ファイルが、いつ、誰に読まれ、何を決めるか。「ここを変えたい」がどのファイルかの対応表。

## 全体図

```mermaid
flowchart LR
  subgraph kit["workflow/kit/（全 PJ 共通・手順）"]
    WF[workflows/*.yml]
    RO[roles/*.md]
    RT[routes.env]
    ST[steps/*.sh]
    SC[schema/*.json]
  end
  subgraph pj["workspace/projects/pj/（PJ 固有・事実と方針。例は examples/projects/）"]
    PY[project.yml]
    GS[gates.sh]
    PV[provision.sh]
  end
  subgraph home["~/.config/sandbox/（秘密・リポジトリ外）"]
    ENV[env]
    PJE[pj/pj.env]
    APP[gh-app/]
    STJ[state.json]
  end
  subgraph kb["workspace/kanban/"]
    DB[(kanban.db)]
    TK[tickets/]
  end
  RN[runner] --> WF & RO & RT & ST & SC & PY & GS & TK
  SB[sandbox CLI] --> ENV & PJE & APP & STJ
  IN[intake] --> RT & PY
  DP[dispatch] --> PY & STJ & DB
```

## 設定ファイル一覧

| ファイル | 誰が読む | 何を決める | 変える頻度 |
|---|---|---|---|
| `workflow/kit/workflows/*.yml` | runner（起動時に schema 検証） | step の並び、担い手、入出力、分岐と上限 | 低。全 PJ に効く |
| `workflow/kit/roles/_common.md` | runner → agent（依頼文 1 層目） | 全役割共通の約束（push しない、範囲外を変えない、秘密を書かない） | 低 |
| `workflow/kit/roles/<role>.md` | runner → agent（2 層目） | 役割の憲法。やること・禁止・出力の形 | 低 |
| `workflow/kit/routes.env` | runner、intake | クラス → モデル（judgment = Fable / research = Sonnet / coding = Opus） | 低。メンテナの判断 |
| `workflow/kit/steps/*.sh` | runner（code step） | gates.sh（PJ のゲートを VM で実行）、pr-create.sh、pr-merge.sh | 低 |
| `workflow/kit/schema/*.json` | runner | workflow yml と project.yml の正しさ | 低 |
| `workspace/projects/<pj>/project.yml` | runner（4 層目）、dispatch（有無だけ）、intake（PJ 一覧）、コンソール | PJ の事実と方針。無ければ `examples/projects/<pj>/` | 中 |
| `workspace/projects/<pj>/gates.sh` | kit/steps/gates.sh（VM 内で） | PJ の品質ゲート | 中 |
| `workspace/projects/<pj>/provision.sh` | proxmox/32-pj-template.sh | テンプレートの焼き込み | 低。テンプレ更新時 |
| 環境変数 `AIFACTORY_WORKSPACE` か `~/.config/aifactory/workspace`（1 行のパス） | kb、intake、dispatch、runner、コンソール | workspace の場所（既定 `<repo>/workspace/`）。環境変数が優先、次に設定ファイル。設定ファイルはシェルを経由しない起動（GUI から開いた Claude Code の MCP、launchd）でも効く。`KB_ROOT` / `CONSOLE_JOBS` で台帳とジョブ記録だけ別に置ける | ほぼ無し |
| `~/.config/sandbox/env` | sandbox CLI、proxmox/run.sh | Proxmox ホスト（`PVE_HOST`）、ゲートウェイ（`GW_SSH`）、鍵、ドメイン、ProxyJump、プールの IP / VMID（`SB_POOL_NET` / `SB_POOL_BASE`）。**リポジトリ外** | ほぼ無し |
| `~/.config/sandbox/pj/<pj>.env` | sandbox CLI（take / reinject） | PJ ごとの `CLAUDE_CODE_OAUTH_TOKEN` と `GH_REPO`。**リポジトリ外** | トークン更新時 |
| `~/.config/sandbox/gh-app/` | sandbox CLI | GitHub App の ID と秘密鍵。**リポジトリ外** | ほぼ無し |
| `~/.config/sandbox/state.json` | sandbox CLI、runner、dispatch、他セッション | どの VM を誰に貸しているか。**リポジトリ外** | take / release のたび |
| `workspace/kanban/kanban.db` + `tickets/` | kb、intake、dispatch、runner（本文） | チケットの状態と本文 | 高 |
| `glue/bin/intake` 内の prompt 文 | intake | 分類の目安、チケットの形 | 低 |

## 優先順位（上書きの規則）

| 何 | 弱い → 強い |
|---|---|
| モデル | 役割の既定クラス → step の `model_class` → 環境変数 `CLAUDE_MODEL`（1 回限り） |
| PR の宛先 | `project.yml` の `base_branch` → workflow の `base_branch: hotfix_base` → `project.yml` の `workflow_overrides.<wf>.base_branch` |
| Claude トークン | `~/.config/sandbox/env`（全体既定）→ `pj/<pj>.env`（PJ 別）→ `SANDBOX_CLAUDE_TOKEN`（1 回限り）。シェルに export された `CLAUDE_CODE_OAUTH_TOKEN` は**無視** |
| GitHub トークン | 静的 `GH_TOKEN`（フォールバック）→ GitHub App の installation token → `SANDBOX_GH_TOKEN`（1 回限り） |
| intake の pj / kind | LLM の判定 → 本文先頭の `pj:` / `kind:` 行 → `--pj` / `--kind` |

## 「ここを変えたい」対応表

| やりたいこと | 触るファイル | 効く範囲 |
|---|---|---|
| 新しい PJ を工場に入れる | `workspace/projects/<pj>/{provision.sh, project.yml, gates.sh}`（`examples/projects/kumitate/` を写す）+ Proxmox でテンプレとプール + `sandbox token set` + GitHub App install | その PJ |
| ゲートを足す / 外す | `workspace/projects/<pj>/gates.sh`。base で既に赤いものは `project.yml` の `known_red_gates` | その PJ |
| agent に毎回伝える事実（テストの走らせ方、既知の罠） | `project.yml` の `facts` | その PJ の全 step |
| この PJ でやってはいけないこと / reviewer が必ず見ること | `project.yml` の `forbidden` / `review_points` | その PJ |
| 手順そのもの（step を足す、差し戻し回数） | `workflow/kit/workflows/<wf>.yml` | 全 PJ |
| 役割の振る舞い（reviewer の見方、implementer の作法） | `workflow/kit/roles/<role>.md`。全役割共通なら `_common.md` | 全 PJ |
| 使うモデル | `workflow/kit/routes.env`（クラス単位）。1 step なら yml の `model_class`、1 回なら `CLAUDE_MODEL` | 指定した範囲 |
| PR の宛先ブランチ | `project.yml` の `base_branch` / `hotfix_base` / `workflow_overrides` | その PJ |
| チケットの分類の癖 | `glue/bin/intake` の prompt 文、または依頼文の先頭に `kind:` 行 | 起票時 |
| プール台数 | `sandbox/proxmox/40-pool.sh`、`glue/bin/dispatch` の `POOL_PER_PJ` | その PJ の並列数 |
| Claude トークンの更新 | `sandbox token set <pj>`（貸出中は `sandbox reinject <id>`） | その PJ |
| Proxmox ホストや IP 空間 | `~/.config/sandbox/env`（`PVE_HOST` / `GW_SSH` / `SB_POOL_NET` / `SB_POOL_BASE`）、Proxmox 側は `SB_NODE` / `SB_NET` / `SB_GW_CT` / `SB_BASE_VMID` / `SB_POOL_BASE`。`sandbox/README.md` の命名規則 + ADR | 全体 |
| 作業データの置き場 | 環境変数 `AIFACTORY_WORKSPACE` か `~/.config/aifactory/workspace` | 全体 |

!!! warning "置き場を間違えやすいもの"
    - 「PJ 固有のことを workflow yml に書きたくなったら間違い」。PJ 固有は `project.yml` の facts / forbidden / review_points に
    - 逆に「全 PJ で毎回同じ注意」を `project.yml` に書くのも間違い。`roles/_common.md` か `roles/<role>.md` に
    - 秘密（トークン、鍵）はリポジトリのどのファイルにも書かない。`~/.config/sandbox/` に

## 変更が効くタイミング

| 変えたもの | いつ効くか |
|---|---|
| workflow yml / roles / routes.env / project.yml / gates.sh | **次の run から**。走っている run には効かない（runner は起動時に読む） |
| `sandbox/bin/sandbox` | `sandbox/bin/install.sh` を実行した後（PATH のコピーが更新される） |
| トークン | `sandbox token set` の後の take から。貸出中は `reinject` |
| `kb` / `intake` / `dispatch` | 即（リポジトリのパスを直接呼ぶ） |
| Proxmox 側スクリプト | 再実行した後 |
