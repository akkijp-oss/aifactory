# console: Web コンソール（Mac ローカル）と MCP サーバー

状態: **v0 実装済み・実運用中（2026-09-06）**。判断の記録は `../docs/adr/0013-web-console-local-stdlib.md`（コンソール）と `0015-mcp-server-shares-core.md`（MCP）。

4 区画（sandbox / kanban / workflow / glue）は全部 CLI で、状態は `$AIFACTORY_WORKSPACE/kanban/kanban.db`・`$AIFACTORY_WORKSPACE/runs/*/state.json`・`~/.config/sandbox/state.json` に散っている。このコンソールはそれらを **1 画面で見て、同じ CLI をボタンで呼ぶ**ためのもの。区画ではなく、区画の上に載る運転盤。

## 起動

```bash
console/bin/console            # http://127.0.0.1:8765/
console/bin/console --open     # ブラウザも開く
console/bin/console --port 9000
```

常駐させるなら launchd に登録する（ログイン時に自動起動、落ちたら再起動）:

```bash
console/bin/install.sh --launchd   # ~/.local/bin/aifactory-console の symlink + ~/Library/LaunchAgents/com.aifactory.console.plist
console/bin/install.sh --remove    # 外す
launchctl kickstart -k gui/$(id -u)/com.aifactory.console   # コードを変えたあと再起動
```

plist には「今のシェルの python3」の絶対パスと、runner が使う `claude` / `gh` / `jq` / `sandbox` の場所を含む PATH を焼く（launchd の既定 PATH は最小で、pyenv の python3 も `claude` も見えない）。`AIFACTORY_WORKSPACE` を既定以外にしているなら plist の環境変数にも入れる。ログは `~/Library/Logs/aifactory-console.log`。

- Python 3 標準ライブラリだけ。npm も pip も要らない（runner と同じ python3 で動かすこと。`yaml` / `jsonschema` は runner が使う）
- 127.0.0.1 にしか bind しない。認証は無いので、他のホストには開けない（`--host` に他を渡すと起動を拒む）
- 止めるのは Ctrl-C。起動中のジョブ（`kb run` 等）はコンソールを止めても続く。記録は `console/jobs/`（git 追跡外。`CONSOLE_JOBS` で差し替え可）に残り、次に起動したときに一覧へ戻る

## 画面

| 画面 | 見るもの | 動かすもの |
|---|---|---|
| ボード | 工程の帯（未着手 → 実行中 → レビュー待ち → 完了、横に人間待ち）と 5 列のカード。動いている run はここに出る | 起票（取り込みへ）/ 配車 1 件 |
| チケット | 本文・履歴・関連する run・ジョブ | `kb run`（dry-run / workflow 上書き / --keep / --resume）/ 状態を進める（`kb start|review|done|reopen|block`）/ `kb set`（種別・PR・メモ）/ `kb sync` |
| 実行記録 | `$AIFACTORY_WORKSPACE/runs/` の一覧。run の工程トラック（step ごとの合否と所要、戻し ↺、終端 end / human）・ファイル・ログ。実行中は `state.json` の `current` が指す step のログを自動で開いて 5 秒ごとに追い読み（ADR-0014） | — |
| sandbox | 貸出中の VM（`~/.config/sandbox/state.json`。アプリの URL つき）と PJ の一覧（project.yml / トークンファイルの有無、プールの使用数） | `sandbox ls`（Proxmox に ssh、数秒）/ `sandbox release <task>` |
| 取り込み | — | 自由文 → `glue/bin/intake` / 整った本文 → `kb new` / `glue/bin/dispatch`（PJ・件数・dry-run） |
| ジョブ | このコンソールが起動した CLI の一覧と出力（2 秒ごとに追い読み） | 止める（プロセスグループに SIGTERM） |
| ログ | `$AIFACTORY_WORKSPACE/logs/intake.log` / `dispatch.log` | — |
| 設定 | workflow の流れ・モデルの経路（routes.env）・PJ 定義の置き場・git status | — |

## MCP（AI セッションからの読み書き）

`console/bin/mcp` は同じ読み書きを MCP のツールとして出す stdio サーバー（標準ライブラリのみ）。リポジトリ直下の `.mcp.json` に登録してあるので、このリポジトリで Claude Code を開くと初回に承認を求められ、以後 `mcp__aifactory__*` として使える。

```bash
claude mcp list                                   # aifactory が見える（プロジェクトスコープ）
claude mcp reset-project-choices                  # 承認をやり直す
claude -p "aifactory の overview を呼んで" --mcp-config .mcp.json --allowedTools mcp__aifactory__overview   # 単発で試す
```

| ツール | 内容 |
|---|---|
| `overview` / `ticket_list` / `ticket_show` | 概況・一覧・1 件（本文・履歴・run・ジョブ） |
| `ticket_new` / `intake` | 起票（整った本文 / 自由文。intake はジョブ） |
| `ticket_action` | start / review / done / reopen / block / set / sync |
| `ticket_run` / `dispatch` | kb run（VM を貸し出して PR まで。dry_run 可）/ todo を順に。どちらもジョブ |
| `run_list` / `run_show` / `read_file` | 実行記録と、限られた根の下のファイル（agent-*.log 等） |
| `sandbox_status` / `sandbox_ls` / `sandbox_release` | 貸出状況 / 実勢（ジョブ）/ 返却（ジョブ） |
| `job_list` / `job_show` / `job_wait` / `job_stop` | ジョブの一覧・出力・待機（上限 570 秒）・停止 |
| `logs` / `config` | glue のログ / workflow・routes・PJ・git |

resources: `aifactory://board`（BOARD.md）、`aifactory://ledger`（台帳）、`aifactory://ticket/<id>`（本文）。

コンソール（HTTP）と MCP は別プロセスだが、読み書きの本体は `lib/core.py` に 1 つで、ジョブ記録 `jobs/` も共有する（`jobs/.lock` の flock で直列化）。どちらから起動したジョブも両方に見える。

## 設計の約束

- **状態を変えるのは必ず既存の CLI**（`kb` / `intake` / `dispatch` / `sandbox`）。コンソールは `kanban.db` を読み取り専用で開き、`state.json` にも書かない。他の AI セッションが同じ CLI を使っている前提（ルート README「同時に別の AI セッションが動いている前提」）を、コンソールも守る
- **長いものはジョブ**。`kb run`（60 分超）や `sandbox ls`（ssh）は `subprocess.Popen` で切り離し、標準出力を `console/jobs/<id>/log` に流す。画面はバイト位置を持って追い読みする。HTTP の応答を待たせない
- **二重起動を防ぐ**。同じチケットの `kb run`、2 本目の `dispatch`（直列の約束）、貸出中 task への `release` はロックの中で弾く（409）
- **読めるファイルを限る**。`$AIFACTORY_WORKSPACE/runs/`・`$AIFACTORY_WORKSPACE/kanban/tickets/`・`$AIFACTORY_WORKSPACE/logs/`・`workflow/kit/`・PJ 定義のディレクトリ（`$AIFACTORY_WORKSPACE/projects/` と `examples/projects/`）・`console/jobs/` だけ。トークンの中身は表示しない（ファイルの有無だけ）
- **POST は `X-Console: 1` ヘッダ必須**。ブラウザの他サイトからは付けられないので、ローカルの他ページからの誤操作を防ぐ
- 破壊的な操作（本番 run / 返却 / 停止）は画面側で確認ダイアログを出す

## 構成

```
console/
├── lib/core.py      # ★読み書きの正本（kanban の読み取り、runs、sandbox、JobStore、操作の判定）。console と mcp が共有
├── bin/console      # HTTP サーバー + JSON API（core の口）
├── bin/mcp          # MCP サーバー（stdio、core の口）。登録はリポジトリ直下の .mcp.json
├── bin/install.sh   # symlink と launchd 登録
├── launchd/         # plist の雛形（install.sh が埋める）
├── static/          # index.html / style.css / app.js（素の HTML/CSS/JS、ビルド無し、hash ルーティング）
├── tests/           # unittest（HTTP API / JobStore / MCP。python3 -m unittest discover -s console/tests）
├── jobs/            # ジョブの記録 <id>/{meta.json,log,stdin.txt}（git 追跡外）
└── README.md
```

置き場の解決（workspace / KB_ROOT / CONSOLE_JOBS）は `../lib/aifactory_paths.py` に 1 つ。

## API（画面が使うもの。curl でも叩ける）

```
GET  /api/overview                 状態の件数・動いている run / ジョブ・貸出数
GET  /api/tickets[?pj=]            一覧      GET /api/tickets/<id>   本文・履歴・run・ジョブ
POST /api/tickets                  kb new    POST /api/tickets/<id>/action {action: start|review|done|reopen|block|set|sync, ...}
POST /api/tickets/<id>/run         kb run をジョブで {dry_run, workflow, keep, resume}
GET  /api/runs  /api/runs/<name>   実行記録  GET /api/file?path=&tail=|offset=   限られた根の下のファイル
GET  /api/sandbox                  POST /api/sandbox/ls   POST /api/sandbox/release {task}
POST /api/intake {text, pj, kind, dry_run}   POST /api/dispatch {pj, once, max, dry_run}
GET  /api/jobs  /api/jobs/<id>?offset=       POST /api/jobs/<id>/stop
GET  /api/logs  /api/config
```

## テスト

```bash
python3 -m unittest discover -s console/tests -v     # HTTP API（複製した kanban で起動して叩く）+ JobStore（ロック・停止・復元）
python3 -m unittest discover -s workflow/tests -v    # runner の逐次ログ（EventRenderer / stream）
```

テストは `KB_ROOT` と `CONSOLE_JOBS` を一時ディレクトリに向けるので本番の DB とジョブ記録を触らない。dry-run が作る `$AIFACTORY_WORKSPACE/runs/…-dry/` はテストが消す。手で試すときも同じ差し替えが使える:

```bash
ws=${AIFACTORY_WORKSPACE:-$PWD/workspace}
cp -r "$ws/kanban" /tmp/kb && KB_ROOT=/tmp/kb CONSOLE_JOBS=/tmp/jobs console/bin/console --port 8799
# workspace ごと隔離するなら AIFACTORY_WORKSPACE=/tmp/ws（projects/ は examples/ から補われる）
```

## 未実装
- 画面確認（スクショ）の回収と表示。`sandbox url` は貸出中の表にリンクとして出る
- 外部の入口（termboard / Notion）からの取り込み。intake に流す層ができれば、その起動ボタンを足すだけ

## 履歴
- 2026-09-06（公開化）: 読む根と状態の置き場を `AIFACTORY_WORKSPACE` に合わせた（`KB_ROOT` / `CONSOLE_JOBS` は従来どおり）
- 2026-09-06（夜・3）: MCP サーバー（`bin/mcp`、ADR-0015）。読み書きの本体を `lib/core.py` に切り出し、HTTP と stdio の 2 口に。ジョブ記録は flock で共有、孤児ジョブは pid の消滅で終了を記録
- 2026-09-06（夜）: 実運用開始。unittest 14 本、`install.sh --launchd` で常駐。コンソールだけで intake → kb run → 実機 VM で research 完走（チケット 206、19 分）を確認
- 2026-09-06（夕）: runner の逐次ログ（ADR-0014）に合わせ、run 画面が実行中 step のログを自動で追うように。ボードの「実行中」に今の step と経過。sandbox に URL 列
- 2026-09-06: v0。ボード / チケット / 実行記録 / sandbox / 取り込み / ジョブ / ログ / 設定。複製 DB で状態遷移・起票・dry-run・停止・再起動後の復元を確認、実機の `sandbox ls` も通した
