# 用語集

本文では日本語で説明していますが、設定ファイルやログには英語の用語が使われます。この表で両者の対応と保存先を確認できます。

| 用語 | 意味 |
|---|---|
| **ADR** | Architecture Decision Record。設計判断の記録。`docs/adr/NNNN-*.md`。1 判断 1 ファイル、書き換えず追記 |
| **agent step** | エージェントが担当する工程。VM 内で `claude -p` を起動し、指定された役割（role）で作業する |
| **artifact** | 成果物。各工程で受け渡すファイル。VM の `~/work/<id>/` に置き、release 時に `runs/…/work/` へ回収 |
| **base**（ブランチ） | 作業ブランチの元と PR の宛先。`project.yml` の `base_branch`（hotfix は `hotfix_base`） |
| **base**（テンプレート） | 全 PJ 共通の VM テンプレート `sb-base`（9100） |
| **blocked** | チケットの状態。人間待ち。メモに理由 |
| **clean** | プール VM のスナップショット名。貸出前の基準状態（アプリ起動済み、firewall 込み、RAM 込み） |
| **code step** | スクリプトが担当する工程。`gates.sh` / `pr-create.sh` / `pr-merge.sh` と、runner 内蔵の `sync-base` |
| **クラス** | 作業の種類。judgment / research / coding。`routes.env` でモデル名に解決 |
| **dispatch** | glue の実行の割り当て。todo を順に `kb run` する。スクリプト |
| **dry-run** | VM を触らず、定義の検証と依頼文の組み立てだけ行う実行 |
| **facts** | `project.yml` の項目。agent に毎回伝える PJ の事実 |
| **gates** | 品質を確認するための検証（lint / typecheck / test / 静的解析）。スクリプトが実行し、失敗したらエージェントに修正を求める |
| **GitHub App** | `aifactory-sandbox`。VM から push / PR するための 1 時間トークンを払い出す |
| **glue** | 区画 4。intake と dispatch |
| **human** | transition の行き先の 1 つ。人間に渡して終了。成果は wip ブランチに退避 |
| **installation token** | GitHub App がリポジトリ限定で払い出す 1 時間有効のトークン |
| **intake** | glue の取り込み。自由文を LLM 1 回でチケットにする |
| **judgment** | クラスの 1 つ。最重要判断。planner / reviewer / intake。モデルは Fable |
| **kanban** | 区画 2。採番・状態・本文。`kb` CLI |
| **kb** | kanban の CLI |
| **known_red_gates** | `project.yml` の項目。base で既に失敗するゲート名。runner が FAIL を INFO（参考情報）として扱うように変更 |
| **PJ** | プロジェクト。対象リポジトリ 1 つに対応。定義は `$AIFACTORY_WORKSPACE/projects/<pj>/`（同梱の例は `examples/projects/<pj>/`） |
| **プール** | 貸し出す VM の集まり。既定は PJ ごと 3 台 + 汎用 3 台 |
| **provision.sh** | PJ テンプレートの作成手順 |
| **project.yml** | PJ の基本情報と作業ルールの定義書 |
| **release** | VM を `clean` に巻き戻して返却 |
| **reinject** | 貸出中の VM にトークンを再注入（巻き戻しなし） |
| **reset** | VM を `clean` に巻き戻す。貸出は継続 |
| **role** | エージェントの役割。計画（planner）、実装（implementer）、調査（researcher）、レビュー（reviewer）がある。行動ルールは `roles/<role>.md` |
| **routes.env** | クラス → モデルの経路表 |
| **run** | runner の 1 回の実行。`$AIFACTORY_WORKSPACE/runs/<日付>-<pj>-<id>/`。kanban の `run` 列にはディレクトリ名だけを記録 |
| **runner** | `workflow/bin/run`。workflow 定義を読んで VM で step を順に実行する Python |
| **sandbox** | 区画 1。隔離された実行環境（VM）とその CLI |
| **sb-gw** | ゲートウェイ LXC（9000）。Tailscale subnet router + dnsmasq + firewall |
| **sbnet** | Proxmox SDN の vnet。`10.77.0.0/16` |
| **step** | 工程。ワークフローを構成する 1 つの作業で、エージェントかスクリプトが担当する |
| **STOP** | planner が危険・不明確な依頼に対して計画の先頭に書く印 |
| **take** | VM を 1 台貸し出す |
| **task-id** | kanban が採番する 3 桁以上の番号。VM 名 `task-<id>`、ブランチ、run に使う |
| **tmpfs** | メモリ上のファイルシステム。VM の `/run/sandbox/env`。巻き戻しで消える |
| **transition** | step の結果に応じた次の行き先。`next` / `on_pass` / `on_fail`。ループ上限つき |
| **wip ブランチ** | `sandbox/<id>-<wf>-wip`。human 行きのとき成果を退避する先 |
| **workspace** | 運用データの置き場。`AIFACTORY_WORKSPACE`（既定 `<repo>/workspace/`、git 追跡外）。`projects/` `kanban/` `runs/` `logs/` `docs/` |
| **workflow** | ワークフロー。チケットの種別ごとに工程の順序と分岐を定めた手順（YAML）。その定義と実行を担当する構成要素の名前でもある |
| **区画** | この工場を 4 つに分けた単位。sandbox / kanban / workflow / glue |
