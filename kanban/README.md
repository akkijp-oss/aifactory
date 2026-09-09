# kanban（区画2）: 何をいつやるか

状態: **v0 実装済み（2026-09-06）**。SQLite + CLI。判断の記録は `../docs/adr/0011-kanban-sqlite-cli.md`。

## 役割
- 組織からの入口。依頼をチケットとして受け、**task-id を採番する**（sandbox の `task-{id}` と workflow の run 名はここから派生）
- チケットの状態管理: `todo → in_progress → review → done`、人間待ちは `blocked`
- 「ただのコード。エージェントは居ない」（動画）。LLM を使う判断（種別の分類、優先順位づけ）はルーター（glue）以降

## 構成

コードはリポジトリ、データは workspace（`AIFACTORY_WORKSPACE`、既定 `<repo>/workspace/`、git 追跡外）。

```
kanban/
├── bin/kb          # CLI（Python 3 標準ライブラリのみ）
└── README.md

$AIFACTORY_WORKSPACE/kanban/
├── kanban.db       # 正本（SQLite）。ID・PJ・種別・状態・PR・run（run ディレクトリ名）・メモ・履歴
├── tickets/        # チケット本文 <id>-<pj>-<slug>.md（1 行目が題名、任意で `pr: N`）。runner にこのパスを渡す
├── attachments/    # チケットの添付 <id>/<名前>（画像・PDF・CSV など）。本文には書かない。run のとき VM に運ばれる（ADR-0041）
└── BOARD.md        # 生成物。状態を変える操作のたびに `kb` が再生成する。手で編集しない
```

正本は 2 つに分かれる: **状態は DB、本文はファイル**。本文をファイルにするのは、runner がそのまま読めること、人間と AI が diff で読めること、履歴が残ることのため。DB は採番の一意性と状態遷移の履歴のため。置き場は `KB_ROOT` で個別に差し替えられる（テスト用。既定は `$AIFACTORY_WORKSPACE/kanban`）。

## 使い方

```bash
kb=kanban/bin/kb
$kb new <pj> <kind> "<題名>" [--body FILE|-] [--pr N] [--note TEXT]   # 起票。id が返る
$kb list [--status S] [--pj P] [--all]                                  # 一覧（既定は done 以外）
$kb show <id>                                                           # メタ + 本文
$kb next [--pj P] [--json]                                              # 次に回す todo を 1 件（glue のルーターが読む）
$kb run <id> [--workflow W] [--dry-run] [--keep] [--resume] [--wait [分]] # workflow/bin/run を呼び、結果で状態を進める
                                                                        # --wait は VM の空きを待つ（分。既定 60）。上限超過は todo に戻す
$kb run <id> --from [STEP] [--branch B]                                 # 人間待ちで終わった run を新しい VM で続きから（既定は記録の resume_step と wip ブランチ。--resume とは併用不可）
$kb sync <id> [--run NAME] [--dry-run]                                  # runs/<NAME>/state.json を読み直して状態を合わせる（--dry-run は書かずに前後を JSON で出す）
$kb run-note <run> [--result done|abandoned] [--pr N] [--text T]        # 人間の後始末（wip から PR 化・マージ／打ち切り）を runs/<run>/state.json に残す
                                                                        # 既に記録があるときは --force を付けたときだけ書き直す
$kb start|review|done|reopen <id> [--note TEXT]                         # 手で状態を進める
                                                                        # done と set --pr は、紐づく run が人間待ちのままなら run 記録にも転記する
$kb block <id> --note "何を待っているか"                                  # 人間待ち
$kb set <id> [--status S] [--pr N] [--run NAME] [--note TEXT] [--kind K] # 任意の項目（`--note ''` でメモを空に戻す）
$kb append <id> [--section S] [--text T]                                # 本文の末尾に追記（--text が無ければ stdin）
$kb attach <id> <file>...                                               # 画像・PDF・CSV などを添付（コピー。本文には書かない）
$kb attachments <id> [--json]                                           # 添付の一覧（名前・サイズ・種別・追加日時）
$kb detach <id> <name>                                                  # 添付を 1 件消す
$kb history <id>                                                        # 変更履歴
$kb render                                                              # BOARD.md を再生成
```

- `pj` は `$AIFACTORY_WORKSPACE/projects/<pj>/`（無ければ同梱サンプル `examples/projects/<pj>/`）がある PJ、`kind` は `workflow/kit/workflows/<kind>.yml` がある種別（chore / bug / feature / hotfix / research / merge-pr）。どちらも起票時に検証する
- `kind` は「何の仕事か」、run の `workflow` は「今回どう回したか」。`kb run --workflow X` は `kind` を書き換えず、履歴に `workflow → X` を残す（種別を変えるのは `kb set --kind`。ADR-0030）
- id は 3 桁以上の連番（`MAX(id)+1`、最小 100）。DNS 名 `task-{id}.sb.internal` に使える
- `run` 列には run ディレクトリ名（例 `2026-09-06-kumitate-206`）が入る。実体は `$AIFACTORY_WORKSPACE/runs/<NAME>/`。`kb sync --run` / `kb set --run` もこの名前で指定する
- `kb run` の結果判定: `pr_url` に MERGED → `done` / PR あり → `review`（人間がレビューしてマージ）/ PR 無しで `end` → `done`（research 等）/ `human` → `blocked`（wip ブランチをメモに残す）/ `failed`（VM が取れず工程が始まらなかった）→ `blocked` / runner 異常終了・記録なし → `blocked`
- take 失敗を `todo` に戻さず `blocked` にするのは、`glue/bin/dispatch` が古い順に `todo` を拾うため。プールが埋まっている間は同じチケットを取り直して失敗し続ける。理由を `note` に残して人間に返し、直したら `kb reopen` → `kb run` で戻す
- `--pr` を変えると本文の `pr:` 行も書き換える（runner は本文の `pr:` を読むため）
- `kb append` は**本文の末尾**に足す。`--section` を付けると `## <見出し>` を先に書く（例 `## PM 補足`）。「`## 完了条件` の手前」には入れない: 節を見分ける仕組みが `kb` に無く、末尾なら diff が 1 か所で済むため。見出しで後から書き足したものだと分かる
- 追記そのものは本文（ファイルが正）に残り、`history` には `body - → append 12字 (PM 補足)` の形で「いつ・どれだけ足したか」だけが残る（`history` は `field/old/new` の 3 列なので差分は持たない）
- `kb set --note ''` はメモを空に戻す（DB は NULL）。`kb` 自体は元から空文字列を通していた。空を「未指定」として無視していたのは MCP / HTTP（`console/lib/core.py`）と画面で、`note` は**キーがあれば空でも渡す・キーが無ければ触らない**に変えた（`status` / `kind` / `pr` は従来どおり空を無視する）
- 添付（`kb attach` / `kb new --attach`）は `attachments/<id>/` にコピーされ、**本文には書かない**（正本は実体のファイル。一覧は `kb show` の末尾・コンソール・MCP `ticket_show` が導く。ADR-0041）
  - 名前は sanitize する（パス区切り・`..`・制御文字を落とす。同じ名前は `-2`, `-3` … を付けて上書きしない）。上限は 1 ファイル 20 MiB・1 チケット合計 100 MiB。判定は `lib/aifactory_attachments.py` に 1 か所
  - `kb run` すると runner が VM の `~/work/<id>/attachments/` に置き、各 step の依頼文に添付の案内が 1 行入る。agent は画像・PDF を Read で開いて見る。添付が無いチケットの依頼文は変わらない
  - **秘密情報（トークン・鍵・`.env` の実値）を添付しない。** `attachments/` は workspace（git 追跡外）なので `bin/oss-check.sh` の秘密情報の検査対象ではない。検査するのは「追跡されていないこと」だけ
  - 今のところ添付を VM に運べるのは Proxmox backend だけ（pull backend＝ macOS / Windows / Linux は別途）。運べない backend では依頼文に案内も出ない
- テストは `KB_ROOT=<別ディレクトリ>`（または `AIFACTORY_WORKSPACE` ごと）で DB・tickets・BOARD の置き場を差し替えて行う

## sandbox / workflow に約束すること
- task-id は `[0-9]{3,}` の文字列
- チケット本文の 1 行目は題名（runner がブランチ名と PR 題名に使う）。`merge-pr` は 2 行目に `pr: N`
- 本文には「完了条件」を書く。種別は `kind` で持つので本文に書かなくてよい

## 取り込み口
自由文からの起票は `../glue/bin/intake`（LLM 1 回で pj / kind / 題名 / 完了条件を決めて `kb new`）。外部システムからは次を intake に流す（未実装）。

| 入口 | 取り込み方 |
|---|---|
| 個人のタスク台帳（外部の個人メモ） | ID 体系が別（既存の task-id と衝突しうる）なので、必要な項目だけ `kb new` で写す |
| termboard の Intent | 相談ひとかたまり → 1 チケット。MCP で読んで `kb new` |
| Notion チケット DB（発注者側） | 発注者が書く。PJ 固有の列対応が要る |
| 音声メモ | 文字起こしを intake に渡せば動くが、当面やらない（メンテナの判断 2026-09-06） |

## 履歴
- 2026-09-06（公開化）: `kanban.db` / `tickets/` / `BOARD.md` を `$AIFACTORY_WORKSPACE/kanban/` へ移し git 追跡外に。`run` 列は run ディレクトリ名を持つ
- 2026-09-06: v0 実装。`workflow/tickets/` にあった 11 件（101〜109、201、202）と v0 spin の依頼文 4 件を取り込み、`workflow/tickets/` を廃止
