# ADR 0052: PJ 定義は MCP から読み書きする。書き先は workspace 側だけで、`examples/projects/` は読むだけ

- 状態: Accepted（ADR-0016 / ADR-0037 / ADR-0038 を補う。既存の決定は変えない）
- 日付: 2026-09-10

## 状況

MCP には PJ 定義（`projects/<pj>/project.yml` と同ディレクトリの `gates.sh` / `provision.sh` / `prepare.sh`）を**読むだけ**の口（`read_file`）しか無かった。

2026-09-09 に PM が 1 つの PJ を Proxmox から `macos-pull` へ切り替えたとき、実際にやったのはこうだった。

- `~/.config/aifactory/mcp-remote.env` の接続先へ ssh する
- `scp` で `project.yml` / `provision.sh` / `gates.sh` を上書きする
- `jsonschema` を手で走らせて、schema に合っているか確かめる

そのあとも「作業ディレクトリ直下にディレクトリを作らない」「screenshot は 8 回まで」といった `facts` を run のたびに足したくなり、そのたびに ssh + scp に戻った。PJ 定義は PM が最も頻繁に触る設定なのに、MCP の外にあった。

置き場は 2 つある（`lib/aifactory_paths.py` の `PROJECT_DIRS`）。

- `$AIFACTORY_WORKSPACE/projects/<pj>/` — 運用データ（ADR-0016）
- `examples/projects/<pj>/` — このリポジトリに同梱したサンプル。`kumitate` と `aifactory` は今のところ **examples 側にしか実体が無い**

## 決定

1. **MCP に `project_show` / `project_read` / `project_write` の 3 つを足す。** 実処理は `console/lib/core.py` に置き、`bin/mcp` はディスパッチだけ（既存の型）。HTTP（`bin/console`）には出さない。
2. **書き先は `$AIFACTORY_WORKSPACE/projects/<pj>/`（`paths.PROJECT_DIRS[0]`）に固定する。** `examples/projects/` はリポジトリの一部なので MCP からは書かない（読みは両方。`paths.project_dir()` の workspace 優先・examples フォールバックのまま）。書き先を組み立てるのは `project_write_dir()` 1 か所だけにし、`paths.project_dir()` の戻り値を書き込みに使わない。
3. **PJ が examples 側にしか無い状態で初めて書くときは、その直下のファイルを一度だけ workspace 側へ複製してから書く**（mode ごと。返りの `seeded_from` / `copied[]`）。以後はその PJ 全体が workspace 側の写しになる。
4. **読み書きできるファイル名は `project.yml` / `gates.sh` / `provision.sh` / `prepare.sh` の 4 つだけ**にし、パス区切り・`.` 始まり・allowlist 外は関数の中で弾く（`ticket_attach_path` と同じ型）。`pj` も同様に検査する（パスの部品になるため）。
5. **書く前に検証し、通らなければ何も書かない。** `project.yml` は `workflow/kit/schema/project.schema.json`、`*.sh` は `bash -n`（一時ファイル・10 秒）。エラーには違反箇所（`json_path`）と `bash -n` の stderr を入れて、呼び手が直せるようにする。
6. **更新前のファイルは同じディレクトリの `<file>.bak-<YYYYmmddTHHMMSS>` に残す**（同じ秒に 2 回なら `-2`, `-3`）。`project_show` はこれを `files[]` ではなく `backups[]` に分けて返す。
7. **書いたあと `*.sh` は 0755、`project.yml` は 0644 にする。**
8. **部分更新のツール（`facts` の append / remove、`backend` だけ差し替え）は作らない。** 呼び手が `project_read` で全文を取り、直した全文を `project_write` に渡す。
9. **annotations（ADR-0037）を付ける。** `project_show` / `project_read` は `readOnlyHint: true`、`project_write` は `false`。`.bak-` を残すので `destructiveHint` は付けない。
10. **`project_show` は `sandbox_ls_refresh_if_stale()` を呼ばない。** `sandbox_view()` の `templates[]` から当該 PJ の 1 件を読むだけにする。

## 理由

- **なぜ部分更新（チケットが求めた `project_set`）を作らないか。** `project.yml` を pyyaml で `safe_load` → `safe_dump` すると、**先頭のコメントとキーの並び順が消える**（`examples/projects/kumitate/project.yml` は 1 行目がコメントで、`facts` / `review_points` / `forbidden` の並びに意味がある）。PM が読み書きする設定ファイルで、ツールを 1 回通すたびに書式が壊れるのは受け入れられない。全文の read → 編集 → write なら本文が保たれ、`gates.sh` を置く口（チケットの `project_put_file`）も「`file` が `*.sh` なら実行ビット」で同じ 1 つに入る。チケットの完了条件は `project_set` → `project_write(file="project.yml")`、`project_put_file` → `project_write(file="gates.sh")` と読み替えて満たす（schema 違反は書かない・`.bak-` を残す・実行ビット、はいずれも満たしている）。
- **なぜ examples に書かないか。** `EXAMPLES` は `REPO / "examples" / "projects"` の固定パスで、`AIFACTORY_WORKSPACE` の影響を受けない。書ける口を開けると、一時 workspace で走らせたテストでもリポジトリの作業ツリーが汚れる。運用でも「同梱のサンプルが手元だけ書き換わり、次の `git pull` で戻る」ことになる。読みのフォールバックは残すが、書きは片側だけにする。
- **なぜ複製するか。** `project.yml` だけを workspace 側に作ると、`paths.project_dir()` はそちらを返すのに `gates:` が指すファイルが無い、という中途半端な PJ ができる。runner はその時点で動かない。複製して初めて「workspace 側の写しが正本」と言い切れる。
- **なぜ書く前に検証するか。** ssh + scp でやっていたときの手順（scp → `jsonschema` を手で実行）を、順序ごと逆にして自動化する。壊れた `project.yml` を置いてから気づくと、その PJ の run が全部落ちる。`workflow/bin/run` の `validate()` は失敗時に `die()`（`sys.exit`）するので関数は再利用できない。schema ファイルだけ共有し、`jsonschema.ValidationError` を `core.ApiError` に変える薄い関数を `core.py` に置く。
- **なぜ `provision.sh` の検証が `bash -n` だけか。** `provision` は `project.yml` の項目ではなく、pull backend（`workflow/lib/macos.py` / `windows.py`）が固定ファイル名で読む規約（ADR-0038）。構文以外に機械が確かめられることが無い。
- **なぜ backend 切替に複数の項目を一度に渡す必要があるか（緩めないか）。** schema は `additionalProperties: false` で、`backend` ごとの `allOf` 条件（`macos-pull` は `worker` 必須・`app_dir` が `/Users/<user>/app`、`windows-pull` は `gates` が `*.ps1`）を持つ。1 回の `project_write` で `backend` / `worker` / `app_dir` を揃えないと通らないが、これは仕様どおりで、揃っていない中間状態を書けてしまう方が危ない。
- **なぜ `.bak-` に残すか。** MCP の呼び手は LLM で、全文を書き直す。取り違えたときに ssh 無しで戻せる必要がある。リポジトリに `.bak` の前例は無いが、`git` で追えない置き場（workspace）だからこそここに要る。

## 結果（トレードオフ）

- MCP だけで「新しい PJ の登録 → backend 切替 → `facts` 追記 → `gates.sh` 差し替え」が回る。ssh も scp も要らない。書いた内容は次の run から効く（runner は制御系の作業ツリーを読む）。
- `.bak-` が溜まる。消す口は用意していない（`project_show` の `backups[]` で見えるので、要るようになったら別途）。
- `workspace/` は `.gitignore` で丸ごと追跡外なので、**チケット本文の「workspace/projects は既に git 管理下」という前提は今のリポジトリでは成り立たない**。書いた内容は git の履歴に残らない。`.gitignore` をこの PR では触らない（ADR-0016 の「運用データはリポジトリの外にも置ける」に対する変更になる）。PJ 定義に履歴が要るなら `AIFACTORY_WORKSPACE` を別リポジトリにするのが筋で、それは運用側の判断として残す。
- Windows backend の `gates.ps1` / `provision.ps1` は allowlist に入れていない（`bash -n` で検証できないため）。`windows-pull` の PJ は `gates` が `*.ps1` でないと schema を通らないので、そのファイル自体はまだ MCP から置けない。要るときに別途足す。
- 呼び手は毎回全文を渡すので、`facts` を 1 行足すだけでも `project_read` → `project_write` の 2 呼び出しになる。書式が保たれる対価としてこれを取る。
