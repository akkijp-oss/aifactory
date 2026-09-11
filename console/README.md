# console: Web コンソールと MCP サーバー

状態: **v0 実装済み・実運用中（2026-09-06）**。判断の記録は `../docs/adr/0013-web-console-local-stdlib.md`（コンソール）、`0015-mcp-server-shares-core.md`（MCP）、`0017-tenant-control-plane-on-proxmox.md`（Proxmox 上の制御系 LXC で常駐・合言葉・`/docs/`）。

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

Linux（テナントの制御系 LXC。ADR-0017）では systemd に登録する。`sandbox/proxmox/25-control-lxc.sh` が呼ぶので手で打つのは更新時だけ:

```bash
CONSOLE_HOST=10.77.0.3 console/bin/install.sh --systemd   # /etc/systemd/system/aifactory-console.service。内側の網なので合言葉は任意（~/.config/aifactory/ctl.env の CONSOLE_TOKEN を置けばかかる）
journalctl -u aifactory-console -f
```

- Python 3 標準ライブラリだけ。npm も pip も要らない（runner と同じ python3 で動かすこと。`yaml` / `jsonschema` は runner が使う）
- 既定は 127.0.0.1 専用・認証なし。**内側の網のアドレス**（10/8・172.16/12・192.168/16・100.64/10 の tailnet・リンクローカル・ULA。グローバルでないもの）には `--host <IP>` だけで合言葉なしで bind できる（ADR-0021。網の境界は tailnet / firewall が守る。制御系 LXC の 10.77.0.3 はこれ）。**0.0.0.0 / :: とグローバルアドレスに bind するには環境変数 `CONSOLE_TOKEN`（合言葉）が必須**（無ければ起動を拒む。ADR-0017）。合言葉を設定すればどのアドレスでも認証がかかる: ブラウザは `/?token=<合言葉>` で 1 回入ると cookie（`aifactory_console`、HttpOnly、SameSite=Strict）に残る。API は `Authorization: Bearer <合言葉>` でも通る。合言葉なしのアクセスは 401
- `/docs/` はドキュメントサイト（`website/site/`、`mkdocs build` の出力）をそのまま配信する。未ビルドなら作り方を出す（503）
- 止めるのは Ctrl-C。起動中のジョブ（`kb run` 等）はコンソールを止めても続く。記録は `console/jobs/`（git 追跡外。`CONSOLE_JOBS` で差し替え可）に残り、次に起動したときに一覧へ戻る

## 画面

ナビは頻度順（ボード / 起票 / 実行記録 / ジョブ / sandbox / 鍵 / ログ / 設定）。`g` + 頭文字で移動、`?` で一覧。画面と文言の約束は `UX.md`（ADR-0019）。

| 画面 | 見るもの | 動かすもの |
|---|---|---|
| ボード | 工程の帯（未着手 → 実行中 → レビュー待ち → 完了、横に人間待ち）と 5 列のカード。動いている run はここに出る | 起票（起票画面へ）/ **配車する**（ダイアログ。PJ・件数・dry-run を選び、押す前に「次に回るチケット」を見せる。未着手が無ければ押せない） |
| チケット | 本文・履歴・関連する run・ジョブ | `kb run`（ダイアログで PJ・workflow・所要を確認。dry-run は離して置く / --keep / --resume）/ 状態を進める（`kb start|review|done|reopen|block`。**確認なし、トーストの「元に戻す」**で前の状態へ。人間待ちだけメモを聞くダイアログ）/ `kb set`（種別・PR・メモ）/ `kb sync`（押す前に対象 run と前後の状態・メモを見せる。run の後にチケットが更新されていれば警告） |
| 実行記録 | `$AIFACTORY_WORKSPACE/runs/` の一覧。run の工程トラック（step ごとの合否と所要、戻し ↺、終端 end / human）・ファイル・ログ。実行中は `state.json` の `current` が指す step のログを自動で開いて 5 秒ごとに追い読み（ADR-0014） | — |
| sandbox | 貸出中の VM（`~/.config/sandbox/state.json`。アプリの URL、その VM で動く run）と PJ の一覧（project.yml の有無、Claude の鍵が鍵プールにそろっているか、プールの定義 / 実体 / 貸出 / 空き）、プール VM の表（貸出先 / VM 名 / IP / PJ / 稼働状態 / 貸出から。取得時刻つき。失敗と未取得を分けて出す） | `sandbox ls`（Proxmox に ssh、数秒。取得中は表示）/ `sandbox release <task>`（危険色のダイアログ。run が動いていれば**チケット番号の入力**） |
| 起票 | — | 自由文 → `glue/bin/intake` / 整った本文 → `kb new`（配車はボードへ移した） |
| ジョブ | このコンソールが起動した CLI の一覧と出力（2 秒ごとに追い読み）。終わると**「次にすること」**（intake → できたチケットを開く / run 停止 → 状態を実行記録に合わせる / 返却 → sandbox） | 止める（ダイアログ。プロセスグループに SIGTERM） |
| 鍵 | Claude の鍵プール（`~/.config/sandbox/keys.json`）の一覧。名前・fable / fable 以外のフラグ・使うかどうか・末尾 4 文字・発行日・最終利用・使用回数・使っている貸出。値は出さない（ADR-0044） | `sandbox keys add / set / token / rm`（子プロセス。ジョブには載せない）。使わない設定にする・消すと、その鍵を使っている貸出に `sandbox reinject` のジョブを起こす |
| ログ | 起票と配車の記録を 1 つの表に（日時・処理・PJ・チケット・結果・理由、新しい順。ログ形式は変えずコンソール側で分解する = ADR-0027）。原文は表の下の「元のログを見る」に畳んで残す（`$AIFACTORY_WORKSPACE/logs/intake.log` / `dispatch.log`） | チケット番号（前方一致）・PJ・種類（起票 / 配車）で絞る（AND、条件は URL に残る）/ チケット番号のリンクでそのチケットへ |
| /docs/ | ドキュメントサイト（ja / en）。工場の使い方を貸出先に渡すのに別ホスティングが要らない | — |
| 設定 | ワークフローの一覧（名前・うまくいったときの流れ・うまくいかなかったときだけ回る工程）、モデルの経路（`routes.env`）、PJ 定義の置き場、`git status`。workflow の名前と工程は本物のリンクで、開くと工程の詳細（担い手・指示・読み書き・上限・分岐・実効モデル）が読める | workflow を開く、工程を開く、定義の原文を読む |

## MCP（AI セッションからの読み書き）

`console/bin/mcp` は同じ読み書きを MCP のツールとして出す stdio サーバー（標準ライブラリのみ）。起動時に `~/.config/aifactory/ctl.env`（`AIFACTORY_CTL_ENV` で差し替え可）を読んで、未設定の環境変数だけ補う。ssh 越し（`mcp-remote`）の非ログイン環境でも、systemd のコンソール（`EnvironmentFile`）と同じ secrets で子プロセスを起こすため（ADR-0030）。`GH_TOKEN` は GitHub App があれば空でよく、runner が `sandbox gh-app token <pj>` で払い出す。リポジトリ直下の `.mcp.json` に **`aifactory-local`**（手元の workspace。VM 無しで試すとき）と **`aifactory-ctl`**（Proxmox 上の制御系。下記）の 2 つを登録してあるので、このリポジトリで Claude Code を開くと初回に承認を求められ、以後 `mcp__aifactory-local__*` / `mcp__aifactory-ctl__*` として使える。運用を制御系 LXC に寄せたら、どのディレクトリからでも使えるように **user スコープ**で `aifactory` の名前で登録するのが楽（`claude mcp add --scope user aifactory -- <repo>/console/bin/mcp-remote`。プロジェクト側の 2 つは承認しなくてよい）。

```bash
claude mcp list                                   # aifactory が見える（プロジェクトスコープ）
claude mcp reset-project-choices                  # 承認をやり直す
claude -p "aifactory の overview を呼んで" --mcp-config .mcp.json --allowedTools mcp__aifactory__overview   # 単発で試す
```

制御系を Proxmox 上の LXC に置いたとき（ADR-0017）は **`aifactory-ctl`**（または user スコープの `aifactory`）を使う。`console/bin/mcp-remote` が ssh 越しの stdio で LXC の `console/bin/mcp` を起動するので、MCP のプロセスは LXC の中で動き、LXC の workspace・kanban・貸出状態を読み書きする（Mac 側の workspace は見ない）。接続先は `~/.config/aifactory/mcp-remote.env` か環境変数で: `AIFACTORY_CTL`（既定 `aifactory@ctl.main.sb.internal`）、`AIFACTORY_CTL_KEY`（既定 `~/.ssh/conf.d/aifactory/sb_ed25519`）、`AIFACTORY_CTL_JUMP`（tailnet 未承認の間は Proxmox ホストの ssh エイリアス）、`AIFACTORY_CTL_REPO`（既定 `~/aifactory`）。別テナントなら `AIFACTORY_CTL=aifactory@ctl.<tenant>.sb.internal`。

```bash
printf 'AIFACTORY_CTL=aifactory@ctl.main.sb.internal\n' > ~/.config/aifactory/mcp-remote.env
claude mcp add --scope user aifactory -- "$PWD/console/bin/mcp-remote"   # Claude Code: どのディレクトリからでも mcp__aifactory__* で制御系を操作
codex mcp add aifactory -- "$PWD/console/bin/mcp-remote"                  # Codex CLI も同じ（~/.codex/config.toml の [mcp_servers.aifactory]）
claude mcp reset-project-choices        # プロジェクト側（aifactory-local / aifactory-ctl）の承認をやり直す。制御系に寄せたなら両方とも承認しなくてよい
```

| ツール | 内容 |
|---|---|
| `overview` / `ticket_list` / `ticket_show` | 概況（`pj` で run の一覧を絞れる）・一覧・1 件（本文・履歴・run・ジョブ。run があれば `sync_preview` も） |
| `ticket_new` / `intake` | 起票（整った本文 / 自由文。intake はジョブ） |
| `ticket_attach` / `ticket_detach` | 添付を 1 件足す（`content_base64` か ctl 上の `path`）/ 消す。`read_file` は画像を image で返す（4 MiB まで） |
| `ticket_action` | start / review / done / reopen / block（`done` と `set` の `pr` は、紐づく run が人間待ちのままなら run 記録にも転記する）/ set（`note` は空文字列で消す）/ append（本文の末尾に追記。`text` 必須・`section` 任意）/ sync（既定は `dry_run: true` で書かず前後を返す。書くのは `dry_run: false` を明示したときだけ） |
| `ticket_run` / `dispatch` | kb run（VM を貸し出して PR まで。dry_run 可）/ todo を順に。どちらもジョブ |
| `run_list` / `run_show` / `read_file` | 実行記録と、限られた根の下のファイル（agent-*.log 等）。`run_show` の `progress` に run 全体と工程ごとの経過秒・今の工程・`work/gates.txt` の PASS / FAIL / INFO 一覧が付く |
| `run_wait` | run の工程が変わる（`until: step`、既定）か終わる（`until: result`）まで待つ（既定 60 秒・上限 300 秒）。変化した瞬間に `{changed, status, step, ok, next, result, pr_url, gate_fails, gates, reason, current, history}` を返す。ログ本文は含まない |
| `run_action` | 人間の後始末（wip から PR 化・マージ／打ち切り）を実行記録に書く（`kb run-note`）。`close` は決着と PR 番号を記録し、`note` は説明を書き直す |
| `sandbox_status` / `sandbox_ls` / `sandbox_release` | 貸出状況（`leases[]` に task / VM 名 / IP / since / 稼働状態）と PJ ごとのプール（定義 / 実体 / 貸出 / 空き）/ 実勢（ジョブ）/ 返却（ジョブ） |
| `job_list` / `job_show` / `job_wait` / `job_stop` | ジョブの一覧・出力・待機（既定 60 秒・上限 300 秒）・停止 |
| `logs` / `config` | glue のログ / workflow・routes・PJ・git |

`tools/list` は全ツールに `annotations`（`title` / `readOnlyHint`、`sandbox_release` / `job_stop` / `computer_close` には `destructiveHint`）を返す。これが無いと Claude Code は「並列に呼べないツール」とみなして同じターンの呼び出しを直列に送るので、サーバーが非同期でも待たされる（ADR-0038）。

### 運転の型

1. `ticket_run(id)` でジョブが返る（`kb run` は 5〜80 分）
2. 工程を追うのは `run_wait(name)`。次の工程遷移まで待ち、変わった瞬間に `step` / `ok` / `next` / `gate_fails` / `pr_url` / `result` を構造化して返す（ログ本文は返さないので、`^\[run ` を grep したりログ本文から終了を判定したりしなくてよい。agent 自身の出力に `result: success` が混ざる）。`timeout_s` に達したら `changed: false` のまま返るので繰り返し呼ぶ。ジョブの側を見るなら `job_show(id, tail=2000)` か `job_wait`。`job_wait` / `run_wait` はどちらも既定 60 秒・上限 300 秒で、Claude Code は 120 秒でバックグラウンド化するのでそれ以上待たせる意味が薄い（ADR-0028 / ADR-0051）。サーバーは待ちを別スレッドに逃がすので他の呼び出しは即応するが、annotations を読まないクライアントでは呼び手の側で直列になる
3. 終わったら `run_show(name)` の `outcome` / `progress`（工程ごとの経過秒とゲートの PASS / FAIL / INFO 一覧）と `ticket_show(id)` の `sync_preview` を見て、詳しくは `read_file(path)` で `agent-*.log` / `code-*.log` / `work/*.md` を読む。経過秒は前の工程が終わった時刻からの差なので、`--from` で再開した run や VM の空き待ちを挟んだ run では工程の外の時間が混ざる（導けないときは `null`）
4. VM の空きは `sandbox_status`。`ls` が 600 秒より古ければ裏で `sandbox ls` を起こし、今回は古い値に `ls_refreshing: true` と `ls_refresh_job` を付けて返す（次の呼び出しで `pool_actual` / `free` が最新になる。起こせなければ `ls_refresh_error`）。貸出中 VM の task / VM 名 / IP / since / 稼働状態は `leases[]` にそのまま出るので、`~/.config/sandbox/state.json` を ssh で読みに行かなくてよい。台帳の場所は環境変数 `SANDBOX_STATE`（`glue/bin/dispatch` と同じ規則）で、無いときは `state_exists: false`（「貸出なし」と読み違えないため）

resources: `aifactory://board`（BOARD.md）、`aifactory://ledger`（台帳）、`aifactory://ticket/<id>`（本文）。

コンソール（HTTP）と MCP は別プロセスだが、読み書きの本体は `lib/core.py` に 1 つで、ジョブ記録 `jobs/` も共有する（`jobs/.lock` の flock で直列化）。どちらから起動したジョブも両方に見える。

## 設計の約束

- **状態を変えるのは必ず既存の CLI**（`kb` / `intake` / `dispatch` / `sandbox`）。コンソールは `kanban.db` を読み取り専用で開き、`state.json` にも書かない。他の AI セッションが同じ CLI を使っている前提（ルート README「同時に別の AI セッションが動いている前提」）を、コンソールも守る
- **長いものはジョブ**。`kb run`（60 分超）や `sandbox ls`（ssh）は `subprocess.Popen` で切り離し、標準出力を `console/jobs/<id>/log` に流す。画面はバイト位置を持って追い読みする。HTTP の応答を待たせない
- **二重起動を防ぐ**。同じチケットの `kb run`、2 本目の `dispatch`（直列の約束）、貸出中 task への `release` はロックの中で弾く（409）
- **読めるファイルを限る**。`$AIFACTORY_WORKSPACE/runs/`・`$AIFACTORY_WORKSPACE/kanban/tickets/`・`$AIFACTORY_WORKSPACE/logs/`・`workflow/kit/`・PJ 定義のディレクトリ（`$AIFACTORY_WORKSPACE/projects/` と `examples/projects/`）・`console/jobs/` だけ。トークンの中身は表示しない（ファイルの有無だけ）
- **POST は `X-Console: 1` ヘッダ必須**。ブラウザの他サイトからは付けられないので、ローカルの他ページからの誤操作を防ぐ
- **摩擦は危険性に比例させる**（ADR-0019、`UX.md` の 4 軸の表が正本）。可逆な操作（状態を進める・戻す、項目を直す）は確認せず、トーストの「元に戻す」で戻す。半可逆・影響大（本番 run、配車）は自前の `<dialog>` で影響を名前と数字で見せる。不可逆・影響大（返却、停止）は危険色 + 既定フォーカスはキャンセル + 必要ならチケット番号の入力。ブラウザ標準の `confirm()` は使わない（ボタンに動詞を書けない）
- **文言は `static/strings.js` に集める**。`app.js` に日本語を直書きしない。ボタンは動詞（「実行する」）、文は「ですます」、用語は `UX.md` の用語集。`tests/test_strings.py` が表記と鍵の対応を検査する（CI でも回る）

## 構成

```
console/
├── lib/core.py      # ★読み書きの正本（kanban の読み取り、runs、sandbox、JobStore、操作の判定）。console と mcp が共有
├── bin/console      # HTTP サーバー + JSON API（core の口）
├── bin/mcp          # MCP サーバー（stdio、core の口）。登録はリポジトリ直下の .mcp.json
├── bin/install.sh   # symlink と launchd（macOS）/ systemd（Linux）登録
├── launchd/         # plist の雛形（install.sh が埋める）
├── systemd/         # unit の雛形（install.sh --systemd が埋める。制御系 LXC 用）
├── static/          # index.html / style.css / app.js（素の HTML/CSS/JS、ビルド無し、hash ルーティング）/ strings.js（画面の文言。全部ここ）
├── tests/           # unittest（HTTP API / JobStore / MCP / 文言。python3 -m unittest discover -s console/tests）
├── UX.md            # 画面と文言の約束 1 ページ（4 軸の表・ボイス&トーン・用語集）。一般論は ../docs/ui-ux-writing-guide.md
├── jobs/            # ジョブの記録 <id>/{meta.json,log,stdin.txt}（git 追跡外）
└── README.md
```

置き場の解決（workspace / KB_ROOT / CONSOLE_JOBS）は `../lib/aifactory_paths.py` に 1 つ。

## API（画面が使うもの。curl でも叩ける）

```
GET  /api/overview[?pj=]           状態の件数・動いている run / ジョブ・貸出数
     pj は run の一覧だけ絞る（上限 limit を掛ける前に絞る。件数は runs_active_n）。counts は常に全 PJ
GET  /api/tickets[?pj=]            一覧      GET /api/tickets/<id>   本文・履歴・run・ジョブ
     どちらも kinds（workflow/kit/workflows/*.yml。`.` / `_` 始まりは出さない）と kind_desc（種別 → 用途）を返す
GET  /api/next[?pj=]               配車で次に回る todo（kb next --json。無ければ null）。配車ダイアログが押す前に見せる
GET  /api/tickets/<id>/sync-preview[?run=]   状態同期の下見（kb sync --dry-run。前後の状態とメモ、run の後にチケットが更新されたか）
POST /api/tickets                  kb new    POST /api/tickets/<id>/action {action: start|review|done|reopen|block|set|append|sync, ...}
     append は {text, section?} で本文の末尾に追記（history に body の行が残る）。set の note はキーがあれば空文字列でも渡す（= メモを消す）
     sync は既定で書かない（dry_run 既定 true。前後を返すだけ）。書くには dry_run: false を明示する
POST /api/tickets/<id>/run         kb run をジョブで {dry_run, workflow, keep, resume}
GET  /api/runs  /api/runs/<name>   実行記録  GET /api/file?path=&tail=|offset=   限られた根の下のファイル
POST /api/runs/<name>/action       人間の後始末を実行記録に書く（kb run-note）{action: close|note, result, pr, text}
GET  /api/sandbox                  POST /api/sandbox/ls   POST /api/sandbox/release {task}
POST /api/tickets/<id>/attach      multipart/form-data（files=…。複数可）   POST /api/tickets/<id>/detach {name}
GET  /api/tickets/<id>/attachments/<name>    添付を返す（画像は inline、他は必ずダウンロード。常に nosniff）
POST /api/intake {text, pj, kind, dry_run}   POST /api/dispatch {pj, once, max, dry_run}
     intake は multipart でも受ける（files=… を <job>/files/ に落として intake の --attach に渡す）
GET  /api/jobs  /api/jobs/<id>?offset=       POST /api/jobs/<id>/stop
GET  /api/keys                     POST /api/keys {action: add|set|rm|token, name, token, fable, other, enabled, note, force}
GET  /api/logs  /api/config
```

## テスト

```bash
python3 -m unittest discover -s console/tests -v     # HTTP API（複製した kanban で起動して叩く）+ JobStore（ロック・停止・復元）+ MCP + 文言（test_strings.py）
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
- 2026-09-07（ADR-0021）: 内側の網のアドレス（10.x 等、グローバルでないもの）には合言葉なしで bind できるように。`CONSOLE_TOKEN` は任意（置けばかかる）。0.0.0.0 とグローバルアドレスは従来どおり必須。メンテナ「internal の場合、多分 localnet だと思うので、認証は不要にしたい」
- 2026-09-07（UX・ADR-0019）: 操作を文脈・頻度・危険性・次の行動の 4 軸で見直し。状態変更は確認なし + 「元に戻す」、配車と本番 run は影響を見せるダイアログ（配車は次に回るチケットを先に出す。`GET /api/next`）、返却と停止は危険色 + 番号入力。ジョブ完了後の「次にすること」、頻度順のナビ、`g` + 頭文字の近道、切断の帯。文言を `static/strings.js` に集約し `tests/test_strings.py` で検査。`UX.md` を追加。サーバー側のエラー文も「何が起きたか + どうすればよいか」に
- 2026-09-06（テナント・ADR-0017）: 合言葉 `CONSOLE_TOKEN`（cookie / Bearer / `?token=`）、`/docs/` でサイト配信、`install.sh --systemd`。Proxmox 上の制御系 LXC で tailnet 向けに常駐できるように。テスト 3 本追加
- 2026-09-06（公開化）: 読む根と状態の置き場を `AIFACTORY_WORKSPACE` に合わせた（`KB_ROOT` / `CONSOLE_JOBS` は従来どおり）
- 2026-09-06（夜・3）: MCP サーバー（`bin/mcp`、ADR-0015）。読み書きの本体を `lib/core.py` に切り出し、HTTP と stdio の 2 口に。ジョブ記録は flock で共有、孤児ジョブは pid の消滅で終了を記録
- 2026-09-06（夜）: 実運用開始。unittest 14 本、`install.sh --launchd` で常駐。コンソールだけで intake → kb run → 実機 VM で research 完走（チケット 206、19 分）を確認
- 2026-09-06（夕）: runner の逐次ログ（ADR-0014）に合わせ、run 画面が実行中 step のログを自動で追うように。ボードの「実行中」に今の step と経過。sandbox に URL 列
- 2026-09-06: v0。ボード / チケット / 実行記録 / sandbox / 取り込み / ジョブ / ログ / 設定。複製 DB で状態遷移・起票・dry-run・停止・再起動後の復元を確認、実機の `sandbox ls` も通した
