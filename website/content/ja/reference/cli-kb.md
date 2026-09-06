# kb（kanban CLI）

`kanban/bin/kb` は、チケットの作成、状態の更新、履歴の確認、runner の呼び出しを行う CLI です。状態と履歴を SQLite に保存します。Python 3 の標準ライブラリだけで動作します。

```
kb new <pj> <kind> <title> [--body FILE|-] [--pr N] [--id N] [--status S] [--note TEXT] [--attach FILE]...
kb list [--status S] [--pj P] [--all]
kb show <id>
kb start|review|done|reopen <id> [--note TEXT]
kb block <id> --note TEXT
kb set <id> [--status S] [--pr N] [--run DIR] [--note TEXT] [--kind K]
kb append <id> [--section S] [--text T]
kb attach <id> <file>...
kb attachments <id> [--json]
kb detach <id> <name>
kb next [--pj P] [--json]
kb run <id> [--workflow W] [--dry-run] [--keep] [--resume] [--from [STEP]] [--branch B] [--force] [--wait [分]]
kb sync <id> [--run DIR]
kb sync --all-review [--pj P]
kb run-note <run> [--result done|abandoned] [--pr N] [--text T] [--force]
kb history <id>
kb render
```

## 置き場

| もの | パス |
|---|---|
| DB（正本） | `$AIFACTORY_WORKSPACE/kanban/kanban.db`（既定 `<repo>/workspace/kanban/`。git 追跡外） |
| 本文（正本） | 同 `kanban/tickets/<id>-<pj>-<slug>.md` |
| 添付（正本） | 同 `kanban/attachments/<id>/<名前>`（ADR-0041） |
| ボード（生成物） | 同 `kanban/BOARD.md` |
| 置き場の差し替え | 環境変数 `AIFACTORY_WORKSPACE=<dir>` で workspace ごと、`KB_ROOT=<dir>` で DB・tickets・BOARD だけを別に置く（テスト用） |

## 状態

| 状態 | 表示 | 意味 |
|---|---|---|
| `todo` | 未着手 | チケット作成済み |
| `in_progress` | 実行中 | runner が実行中 |
| `review` | レビュー待ち | PR あり。人間待ち |
| `blocked` | 人間待ち | human 行き、異常終了、project.yml なし。メモに理由 |
| `done` | 完了 | マージ済み、または PR なしで終了 |

## コマンド

### new

```bash
kb new <pj> <kind> "<題名>" [--body FILE|-] [--pr N] [--id N] [--status S] [--note TEXT]
```

| 引数 | 意味 |
|---|---|
| `pj` | プロジェクト定義ディレクトリ（`$AIFACTORY_WORKSPACE/projects/<pj>/`、なければ `examples/projects/<pj>/`）があること |
| `kind` | `workflow/kit/workflows/<kind>.yml` に一致すること（chore / bug / feature / hotfix / research / merge-pr） |
| `title` | 1 行目。70 字で切る |
| `--body` | 本文ファイル。`-` で標準入力。なければ本文なし |
| `--pr` | merge-pr の対象 PR 番号。本文 2 行目に `pr: N` として書かれる |
| `--id` | 番号を指定（既定は `MAX(id)+1`、最小 100）。既にあればエラー |
| `--status` | 初期状態（既定 todo） |
| `--note` | メモ |

`<id> <状態> <pj> <kind> <PR> <題名>` の 1 行と、本文ファイルのパスを出力します。ファイル名の slug は題名に含まれる ASCII 文字から作ります。該当する文字がなければ `kind` を使います。

### list / show / next

```bash
kb list                          # done 以外
kb list --status review          # 状態で絞る
kb list --pj kumitate --all      # PJ で絞る。--all で done も
kb show 204                      # 全項目 + 本文
kb next                          # 最も古い todo を 1 件
kb next --pj kumitate --json     # JSON（dispatch や外部ツール向け。path に本文の絶対パス）
kb resumable [--pj P] [--json]   # 鍵の利用枠切れで一時停止中のチケットと、解除時刻を過ぎて続きを回せるか（dispatch --resume-paused が読む。ADR-0043）
```

### 状態を進める

```bash
kb start 204                     # → in_progress
kb review 204                    # → review
kb done 204 --note "マージ済み"    # → done
kb block 204 --note "本番影響の判断待ち"   # → blocked（--note 必須）
kb reopen 204                    # → todo
kb set 204 --status review --pr 300 --run 2026-09-06-kumitate-204 --note "…" --kind feature
```

`--pr` を変更すると、runner が参照する本文の `pr:` 行も更新されます。`--run` には、`workspace/runs/` からの相対パスで run ディレクトリ名を指定します。`--kind` は、指定した種別が存在するか確認されます。変更はすべて履歴に残り、`BOARD.md` が再生成されます。

`kb set 204 --note ''` はメモを空に戻します（DB では NULL）。項目を渡さなければその項目は変更しません。MCP と HTTP API（`console`）では、`note` は「キーがあれば空文字列でも渡す（= 消す）、キーがなければ触らない」として扱います。以前は空文字列を未指定として無視していました。`status` / `kind` / `pr` は従来どおり、空文字列を未指定として無視します。

### append

```bash
kb append 204 --section "PM 補足" --text "218 の `._*` は AppleDouble"
kb append 204 --section "PM 補足" < memo.md      # --text がなければ標準入力から読む
```

チケット本文の**末尾**に追記します。`--section` を付けると `## <見出し>` を先に書きます。本文が空、またはチケットの本文ファイルがなければエラー（終了コード 1）です。

挿入位置は末尾に固定しています。`## 完了条件` の手前に入れないのは、節を見分ける仕組みが `kb` になく、末尾なら変更が 1 か所で済むためです。後から書き足したものは見出しで見分けます。

追記そのものは本文（ファイルが正本）に残ります。履歴には `body  - → append 24字 (PM 補足)` の形で、いつ・どれだけ足したかだけが残ります（`history` は `field` / `old` / `new` の 3 列で、差分は保持しません）。

### attach / attachments / detach

```bash
kb attach 204 ~/Desktop/画面.png 仕様書.pdf    # コピーされる（元のファイルは残る）
kb attachments 204                             # 名前・サイズ・種別・追加日時
kb attachments 204 --json                      # [{"name","size","type","added"}]
kb detach 204 画面.png
kb new kumitate bug "不具合: 保存が効かない" --body - --attach 画面.png   # 起票と同時に
```

チケットに画像（スクリーンショット・デザイン案）やファイル（仕様書 PDF・CSV・設定ファイル）を添付します。`$AIFACTORY_WORKSPACE/kanban/attachments/<id>/` にコピーされ、**本文には書きません**。正本は実体のファイルで、一覧は `kb show` の末尾・コンソールのチケット画面・MCP `ticket_show` が実体から導きます（ADR-0041）。

- 名前は sanitize されます（パス区切り・`..`・制御文字を落とし、Markdown の記法（`` ` `` `*` `[` `]` `<` `>` `|`）は `_` に置き換え、連続する空白は 1 つにして、120 バイトに切ります）。名前は各工程の依頼文にそのまま埋まるので、読める長さと文字種に締めています。同じ名前の添付があれば拡張子の前に `-2`, `-3` … を付け、上書きしません
- 上限は **1 ファイル 20 MiB・1 チケット合計 100 MiB**。超えるとエラー（終了コード 1）です。複数指定したときは失敗したファイルで止まり、そこまでに入った分は残ります
- 追加・削除は履歴に `attachment  - → add 画面.png (12.3 KiB)` / `attachment  画面.png → removed` の形で残ります
- 添付が 0 件のチケットでは `kb show` の出力は今までと変わりません
- `kb new --attach` は **チケットは作れたのに添付だけ失敗する**ことがあります（上限を超えたときなど）。そのとき id は標準出力に出ますが終了コードは 0 ではありません。チケットは在るので、添付だけ `kb attach <id> <ファイル>` でやり直してください

`kb run` すると、runner が添付を VM の `~/work/<id>/attachments/` に置き、各 step の依頼文に次の 1 行を足します。

```
- 添付: /home/dev/work/204/attachments/（画面.png, 仕様書.pdf。画像・PDF は Read で開いて見ること。本文と食い違うときは添付を優先し、その旨を報告に書く）
```

agent（Claude Code）は Read ツールで画像（PNG / JPG など）と PDF を開けるので、「この画面のここ」「この表のとおりに」を実物で渡せます。添付が無ければ依頼文には何も足しません。

!!! warning "秘密情報を添付しない"
    `attachments/` は workspace（git 追跡外）なので、`bin/oss-check.sh` の秘密情報の検査対象ではありません（検査するのは「git に追跡されていないこと」だけ）。トークン・鍵・`.env` の実値は添付しないでください。

!!! note "今は Proxmox backend だけ"
    添付を VM に運べるのは Proxmox backend だけです。pull backend（macOS / Windows / Linux ワーカー）は転送の口が別なので未対応で、依頼文にも案内は出ません。

### run

```bash
kb run 204 [--workflow W] [--dry-run] [--keep] [--resume] [--from [STEP]] [--branch B] [--force] [--wait [分]]
```

1. `pj` に `project.yml` がなければエラー。`done` は `--dry-run` 以外エラー（`reopen` してから）
2. 状態を `in_progress` に、run ディレクトリ名（`<日付>-<pj>-<id>`。実体は `workspace/runs/` の下）を記録
3. `workflow/bin/run <pj> <id> <workflow> <本文のパス> [flags]` を呼ぶ。`--workflow` は今回の実行方法だけを変え、`kind` は変わりません（履歴に `workflow → <名前>` が残り、`runs/<run>/state.json` の `workflow` が正。ADR-0030）。`--workflow` なしの `--resume` は、その `state.json` の `workflow` で再開します
4. 終わったら `state.json` を読んで状態を進める（下表）

`--wait` を付けると、VM のプールに空きがないときに失敗せず、空くまで待ってから実行します（分。値を省くと 60 分）。待っている間、チケットは `in_progress` のままで、コンソールとボードには「VM の空き待ち」と経過時間が出ます。上限を超えたときだけチケットは `todo` に戻り、理由がメモに残ります（ADR-0031）。

`--from` を付けると、人間待ちで終わった run を**新しい VM**で、その続き（記録の退避ブランチ）から指定の工程だけやり直します。工程を省くと、記録に残った「やり直す工程」から始まります。前回の run の名前は環境変数で runner に渡り、前回の成果物が新しい VM に持ち込まれます（ADR-0036）。前回のレビュー指摘が依頼文に入るのは、前回の `review.md` が FAIL だったときだけです（ADR-0053）。`--resume` とは併用できません。

!!! warning "同じチケットの二重再開は断ります"

    退避ブランチの名前はチケットと workflow で決まるので、同じチケットを 2 本同時に `--from` で再開すると、後から保全した方が先の run の成果を上書きします。台帳が実行中で、その run の記録もまだ終わっていない（`state.json` に `finished` が無い）ときは、`kb run --from` はエラーで止まります。終わっているのに台帳が古いだけなら `kb sync <id>` で合わせてください。承知の上で通すときだけ `--force` を付けます（ADR-0053）。

| state.json | 状態 | メモ |
|---|---|---|
| `pr_url` に `MERGED` | done | マージ済み URL |
| `pr_url` あり | review | PR 待ち URL |
| `result: end`、PR なし | done | PR なしで終了（research 等） |
| `result: human`、PR なし | blocked | 人間へ（wip ブランチ） |
| `result: human`、`failure: quota`（鍵の利用枠切れ） | **todo** | 一時停止。`retry_after`（解除時刻）以降に `dispatch --resume-paused` が `kb run --from` で続きを回す。`quota_hits` が `AIFACTORY_RESUME_MAX_HITS`（既定 6）に達したら blocked |
| `result: human`、`failure: key`（鍵が無効・失効・残高不足） | blocked | 鍵を直してから `kb run --from`（メモにコマンド） |
| `result: failed` | blocked | VM を取得できず工程が始まらなかった（`error` の最終行をメモに残す） |
| `result: failed`、`failure: wait_timeout` | todo | `--wait` の上限まで待っても空きが出なかった。直す所はないので未着手に戻す |
| `finished` なし、runner が rc≠0 | blocked | runner が記録を残さず終了 rc=N |

`--dry-run` ではチケットの状態を変更しません。終了コードは runner の値をそのまま返します。0 は正常終了または PR の作成完了、2 は PR がない状態での `human` 終了を表します。

同じチケットを回し直すとき（今日の同じ名前の run がすでにある、または `--from` / `--branch` を付けたとき）は、メモの run 行を `[run] 再走中（attempt N・workflow W）` に置き換えます。置き換えないと、前回人間待ちで終わったときの「人間へ（wip: …）…」が実行中のメモとして残り、一覧が古い状態に見えます。前の run 行は `kb history` に残ります。初回の run ではメモを触りません。

上の表の「メモ」は、チケットのメモを丸ごと置き換えるのではなく、**1 行目の `[run] ` で始まる行だけ**を書き換えます（`kb run` / `kb sync` / 再走のどれも同じ規則。ADR-0048）。2 行目以降は人のもので、run は消しません。人が `kb set --note` / `kb block --note` などで書くときは今までどおりメモ全体を書き（`--note ''` で空に戻せます）、次の run が自分の 1 行目を書き直します。

```
[run] PR 待ち https://github.com/akkijp/kumitate/pull/300   ← 機械が書き換える 1 行
Mac (Claude Code MBP) で実施。Linux sandbox は gates 赤のため   ← 人が書いた申し送り（消えない）
```

メモは**状態の要約**です。長い申し送りはチケット本文の `## PM 補足` に（`kb append --section "PM 補足"`）残してください。`[run] ` を付ける前に書かれた run 由来の文言（`人間へ…` / `PR 待ち…` など）が 1 行目にある既存のチケットは、次の `kb run` / `kb sync` でその行を捨てます。データの移行は要りません。

### run-note

```bash
kb run-note 2026-09-06-kumitate-204 --result done --pr 300 --text "wip から PR を作ってマージした"
```

人間が run の後始末（wip ブランチから PR を作ってマージした、または打ち切った）をしたことを実行記録に残します。`runs/<run>/state.json` に `human: {at, by, result, pr_url, text}` を足すだけで、runner が確定した `result` は変えません（ADR-0039）。

| 項目 | 内容 |
|---|---|
| `--result` | `done`（人間が仕上げた。既定）か `abandoned`（打ち切った） |
| `--pr` | 人間が作ってマージした PR の番号。PJ 定義に `repo` があれば URL に、無ければ `#N` になります |
| `--text` | 後始末の説明。コンソールの run 画面に出ます |
| `--force` | すでに人間の記録がある run に書き足す（省いた項目は前の記録のまま） |
| `by` | 環境変数 `AIFACTORY_ACTOR`、無ければ `USER` |

`finished` のない（runner が動いている）run と、すでに `human` のある run（`--force` なし）は断ります。コンソールは `#/run/<name>` の「結果」に「人間が PR #n で仕上げました（完了）。」を出し、続きから回すコマンドを出さなくなります。

`kb set <id> --pr N` と `kb done <id>` は、そのチケットの run が人間待ちのままで、まだ人間の記録が無ければ、同じ内容を自動で転記します（コンソールのチケット画面と MCP の `ticket_action` も同じ道を通ります）。runner 自身が作った PR は転記の対象にしません（`kb sync` からは呼びません）。転記できないときはチケットの更新だけを行い、理由を `[kb] warn:` として出します。

### sync

```bash
kb sync 204 [--run DIR]
kb sync --all-review [--pj P] [--dry-run]
```

`state.json` を読み直し、チケットの状態に反映します。runner を直接呼び出した場合、`kb run` が途中で終了した場合、別のセッションの実行結果を反映する場合に使います。`finished` がなければ状態は `in_progress` とし、次の工程をメモに記録します。

そのうえで、チケットが `review` で PR 番号が入っていれば、**その PR が GitHub でどうなったか**を `gh pr view` で確かめます（ADR-0050）。run に紐づいていないチケット（人が板で PR を付けたもの）も、`review` かつ PR があれば同期できます。run も PR も無いときだけ断ります。

| PR の状態 | チケット | メモの 1 行目 |
|---|---|---|
| `MERGED` | `done` | `[run] PR #n マージ済み <mergedAt>` |
| `CLOSED`（未マージ） | `blocked` | `[run] PR #n がマージされずに閉じられた <closedAt>。作り直すなら kb reopen <id> → kb run <id>` |
| `OPEN` / 不明 / 問い合わせ失敗 | 変えない | 触らない |

判定に使うのは `state` と `mergedAt` の 2 つだけです。誤って `done` にするのが最大の害なので、分からないときは何もしません。日時は GitHub が返した値（`Z` 付き）をそのまま書きます。

```bash
kb sync --all-review              # review のチケット全件（全 PJ）
kb sync --all-review --pj asura   # PJ を絞る
kb sync --all-review --dry-run    # 書かずに、変わる予定のチケットを 1 件 1 行の JSON で出す
```

`--all-review` は `review` のチケットを id 順に回し、PR の状態だけを見ます（run の再判定はしません）。最後に要約を 1 行出します。

```
[kb] sync --all-review: 対象 5 件 / done 2 / blocked 1 / 変更なし 2 / 飛ばした 0
```

`gh` の認証は PR 作成と同じ PJ 別の GitHub App（ADR-0008 / ADR-0030）を使い、トークンは PJ ごとに払い出します（App のトークンはリポジトリ限定のため）。`GH_TOKEN` が環境変数にあればそれをそのまま使います。**App も `GH_TOKEN` も無い環境（開発機・CI）では黙って飛ばします**: チケットは変えず、終了コードは 0 のまま、理由を `[kb] warn:` として標準エラーに 1 行だけ出します（`--all-review` では PJ ごとに 1 行）。

PR 由来の更新でも run 記録への転記（ADR-0039）はしません（`kb sync` は転記しない、という規約のままです）。

### history / render

```bash
kb history 204                   # 日時 / 項目 / 旧 → 新
kb render                        # BOARD.md を再生成（通常は自動）
```

## 終了コード

| コード | 意味 |
|---|---|
| 0 | 成功 |
| 1 | 引数や存在チェックのエラー（`[kb] error: …` を標準エラーに） |
| runner のコード | `kb run` は runner の終了コードをそのまま返す |

## 実装メモ

- テーブルは `tickets`（id, pj, kind, title, status, file, pr, run, note, created, updated）と `history`（ticket, at, field, old, new）
- 単一 Mac 前提。複数マシンで同じ DB を書くと衝突する
- 判断はしない。LLM を呼ばない
