# workflow: どう進めるか（AI developer workflow の定義と runner）

sandbox（どこで動くか）の上で、**誰（agent / code）が・何を受け取り・何を出し・次に誰か**を定義し、そのとおりに回す区画。動画の主張「ループを作るのではなく workflow を設計せよ。エンジニアは冒頭の計画と末尾のレビューにだけ出る」をファイルに落としたもの。

判断の記録: `../docs/adr/0009-workflow-definition-format.md`（形式と置き場）、`0006`（トークン）、`0008`（GitHub App）。

## 語彙（5 つ）

| 語彙 | 意味 | 置き場 |
|---|---|---|
| **workflow** | チケット種別ごとの手順。step の並びと分岐 | `kit/workflows/<name>.yml` |
| **step** | 1 回の呼び出し。担い手は **role**（agent）か **code**（スクリプト）のどちらか | workflow の中 |
| **role** | agent の人格と権限。モデルのクラス、憲法、出力の型 | `kit/roles/<role>.md`（クラス→モデルは `kit/routes.env`） |
| **artifact** | step の入出力。**必ずファイル**。VM の `~/work/<task>/` に置き、終了時に `$AIFACTORY_WORKSPACE/runs/<run>/work/` へ回収 | 名前は workflow の `inputs` / `outputs` |
| **transition** | 結果に応じた次の行き先。ループ回数の上限つき。`human` = 人間に渡して終了 | step の `next` / `on_pass` / `on_fail` |

## 常備（kit）と PJ 固有（projects/<pj>）

```
workflow/
├── kit/                          # ★常備。全 PJ 共通。手順の形はここにしか無い
│   ├── schema/                   # workflow.schema.json / project.schema.json（YAML の正しさを機械で検証）
│   ├── roles/                    # _common.md（全役割共通の約束）+ planner / implementer / reviewer / researcher
│   ├── workflows/                # hotfix / bug / feature / chore / research / merge-pr
│   ├── steps/                    # code step: gates.sh（PJ のゲートを VM で実行）/ pr-create.sh（push + PR）/ pr-merge.sh
│   │                             # pr-automerge.sh（条件を確かめて PR を base へマージ。ADR-0041）
│   │                             # `sync-base`（PR 直前の base 取り込み）は runner 内蔵で、ここにファイルは無い
│   └── routes.env                # クラス → モデル（judgment=Fable / research=Sonnet / coding=Opus）
├── bin/run                       # runner v1（Python）。定義を読んで VM の中で step を順に実行する
├── bin/spin-v0.sh                # v0 スパイク（hotfix 相当をベタ書き）。参考として残す
├── prompts/                      # v0 で使った依頼文
└── tests/                        # unittest（逐次ログ）

$AIFACTORY_WORKSPACE/                 # 運用データ（既定 <repo>/workspace/、git 追跡外）
├── runs/<date>-<pj>-<task>/      # 実行記録: ticket.md / state.json / prompt-*.md / agent-*.log / code-*.log / work/（回収した artifact）
│                                 # state.json は ticket.md と同時に作る。take（VM 取得）で落ちた run にも `result: failed` と `error`（理由）が残る
│                                 # agent-*.jsonl は生イベント。agent-*.log / code-*.log は step の途中から逐次書かれる（ADR-0014）
│                                 # このディレクトリ名が kanban の run 列に入る
├── kanban/tickets/               # チケット本文（採番と状態は kanban）
└── projects/<pj>/
    ├── project.yml               # ★PJ 固有。事実（repo / base / app_dir / gates / facts）と方針（review_points / forbidden）だけ
    ├── gates.sh                  # PJ のゲート（VM 内で実行。全緑なら 0。引数があればその名前のゲートだけ）
    ├── prepare.sh                # 任意。貸出直後の準備（project.yml の `prepare:` で名前を指定）
    └── provision.sh              # テンプレート焼き込み（sandbox 区画）

examples/projects/<pj>/           # 同梱サンプルの PJ 定義（kumitate）。workspace に同名の PJ が無いときに使われる
```

**手順を PJ ごとに複製しない**のが肝。PJ 固有は「どこに何があるか」「何を守るか」に限り、workflow を育てる場所を 1 つにする。PJ 定義の探索順は workspace → examples。

## 形式

- 骨格は **YAML**（コメント可・複数行可）。正しさは **JSON Schema** で検証（runner が起動時に必ず行う）
- 文章（役割の憲法・step の追加指示）は **Markdown**（憲法は別ファイル、追加指示は YAML の `brief:` にブロックで）
- 機械が生成・受け渡しするもの（`state.json`、artifact の索引）は **JSON**
- YAML の平文にバッククォートや `: ` を含める項目は `"..."` で囲む（PyYAML が誤読する）

## 使い方

```bash
# 通常は kanban から（採番・状態更新つき）
kanban/bin/kb new kumitate bug "題名" --body ticket.md   # チケット起票 → id が出る
kanban/bin/kb run <id> [--dry-run]                          # runner を呼び、結果で状態を進める
# runner を直接呼ぶとき（kanban を通さない実験用）
workflow/bin/run <pj> <task-id> <workflow> <ticket.md> [--dry-run] [--keep] [--resume] [--from[=step]] [--branch=名前] [--wait[=秒]]
workflow/bin/run kumitate 900 hotfix ticket.md --dry-run     # VM を触らず定義と依頼文だけ確認
```

- `--keep` は終了後に release しない（中を見たいとき）、`--resume` は貸出中の VM で state.json の次の step から続ける
- `--from[=step]` は human で止まった run を**新しい VM**で指定の step からやり直す（`--branch=<名前>` の続きから。既定は前回の `wip_branch`）。step を省くと前回の `resume_step` を使う。前回の run は `AIFACTORY_FROM_RUN`（run 名の形だけ）で渡し、その `work/*.md` を持ち込み、`review.md` を最初の依頼文に「前回の結果（直すこと）」として入れる（ADR-0036）
- `--wait` はプールに空きが無いとき失敗せず空くまで待って take し直す（単独なら 3600 秒、`--wait=秒` で上限。間隔は `AIFACTORY_WAIT_POLL_S` 秒・既定 30）。待機中は `current` が `wait-vm`、上限超過は `failure: "wait_timeout"` を書いて終わり `kb` がチケットを todo に戻す

runner がやること: `sandbox take` → 作業ブランチ作成 → step を順に（agent は `claude -p --model <クラスのモデル> --output-format stream-json` を VM 内で実行、code は制御系で `kit/steps/*.sh`）→ transition → artifact 回収 → `sandbox release`。PR は `pr-create.sh` が作り、**マージは人間**（`project.yml` に `auto_merge` を書いた PJ だけ、次の `automerge` が条件を確かめて機械がマージする）。

`pr` の直前には `sync` step（`code: sync-base`。runner 内蔵）が入り、`git fetch origin <base>` と `git merge` で **base の最新を取り込む**。並列に走った別 run の PR が先にマージされても、後発の PR が CONFLICTING で出てこないようにするため（ADR-0032）。衝突したら `git merge --abort` して衝突ファイル名を添え、implementer の `resolve` step に戻す（最大 2 回。3 回目で `human`）。取り込みの後に `docs/adr/` の番号重複も検査する（別ファイルなので git は衝突と見なさないため）。衝突が無ければ gates は回し直さず PR へ進む。

step の出力は終了を待たず `runs/<run>/agent-<step>-<n>.log` / `code-<step>-<n>.log` に逐次書かれる（agent は `[+MM:SS] ▶ ツール: 引数` / `↳ 結果の先頭` / `result: … cost=$…` の形。生の JSON は同名 `.jsonl`）。`state.json` の `current` が今動いている step とログ名なので、`tail -f` か Web コンソール（`../console/`）で追える（ADR-0014）。

agent step への依頼文は runner が組み立てる: 共通の約束 → 役割の憲法 → step の `brief` → project.yml の事実・禁止・観点 → チケット → `inputs` の中身 → 出力先の指定。組み立て結果は `runs/<run>/prompt-<step>-<n>.md` に残るので、あとから読める。

## テスト

```bash
python3 -m unittest discover -s workflow/tests -v    # 逐次ログ（EventRenderer / stream）。VM も claude も使わない
```

VM 無しで runner を 1 周させたいときは、`sandbox` と `scp` のシムを PATH に置き、`HOME` を差し替えて偽の `~/.config/sandbox/state.json` を読ませる（手順は ADR-0014 の「実測」）。`AIFACTORY_WORKSPACE` を一時ディレクトリに向ければ本番の run 記録を汚さない。

## 貸出直後の準備（`prepare`）と、base でも赤いゲート

VM は run が終わるたびにテンプレート（`provision.sh` を焼いた時点）へ戻り、base だけが進む。このずれが gates の赤として
出ると、実装役は「自分が壊した」と思って直せないものを直そうとする。対処は 2 つに分かれる（ADR-0038）。

**環境のずれは `prepare` で埋める。** `project.yml` に `prepare: prepare.sh` と書くと、runner が VM を取って checkout した
直後、最初の agent step の前に `app_dir` を cwd にして 1 回だけ実行する（`--from` の続きや PR 起点の run でも走らせる）。

```yaml
gates: gates.sh
prepare: prepare.sh       # 例: pnpm install --frozen-lockfile && pnpm --filter @kumitate/db db:migrate
```

失敗したら **agent を起動せずに終わる**（`state.json` に `result: failed` / `failure: "prepare"`、`history` は空、出力は
`code-prepare.log`）。整っていない VM で agent を起こしても直せない赤を直そうとするだけなので、人へ返す（kb はチケットを
`blocked` にする）。pull backend（macOS / Windows / Linux）は既存の `provision.sh` を毎 take 実行していて、同じ役目を果たす。

**準備では直らない赤（base 自身が赤い）は、runner が base で回して確かめる。** gates が赤いと、`kit/steps/gates.sh` が
その**赤いゲートだけ**を `origin/<base>` でも実行する（同じ作業コピーで checkout を差し替える。未コミットの変更は
`git stash` で退避して必ず戻す）。base でも赤かったものは実装役に戻さない。

```
INFO strings red (also red on base; not a gate)
PASS feature

=== base check: origin/develop
BASE-CHECK origin/develop 675cdbc
FAIL strings (~/gates/strings.log)
```

残りに `FAIL` が無ければ gates は PASS で review へ進み、implement への戻しを消費しない。確かめたゲート名は run の記録
（`state.json` の `known_red_gates`）に載り、`sandbox_status` が `project.yml` に人が書いた値と合わせて見せる。
`project.yml` は書き換えない。base で全ゲートを回し直さないために、PJ の `gates.sh` は
**引数があればその名前のゲートだけ走らせる**契約にしてある（引数なしは従来どおり全部）。

base を見に行けなかった回（未コミットの変更を退避できない、`origin/<base>` が無い）は `=== base check:` に
`BASE-CHECK-SKIP` と理由が出て、判定はしない（実装役への依頼文もその回だけ言い切らない）。base を見た後に作業ブランチへ
戻し切れなかったときは、赤が残っていなくても run をそこで止めて人へ回す（base に居る作業ツリーで agent を走らせない）。

## 6 つの workflow

| 名前 | 流れ | 使いどころ |
|---|---|---|
| hotfix | plan(Fable) → implement(Opus) → gates → review(Fable) → sync → pr → automerge | 本番障害の最小修正。宛先は `hotfix_base` |
| bug | plan → implement（再現テスト先行）→ gates → review → sync → pr → automerge | 不具合修正 |
| feature | research(Sonnet) → design(Fable) → implement → gates → review → sync → pr → automerge | 機能追加 |
| chore | implement → gates → sync → pr → automerge | docs 整理・依存更新など判断の要らない雑務 |
| research | research(Sonnet) → judge(Fable) → end | 調査だけ。PR 無し。`summary.md` を回収 |
| merge-pr | resolve → gates → review → merge | 既存 PR のコンフリクト解消とマージ。本文の `pr: N` 行で対象を指定 |

gates が赤なら implementer に戻す（最大 2 回）、review が FAIL なら戻す（最大 1 回）、超えたら `human`。

### `automerge`（`auto_merge` のある PJ だけ。ADR-0041）

`pr` の後ろに入る code step（`kit/steps/pr-automerge.sh`）。`project.yml` に `auto_merge` が無い PJ では runner が
**step ごと飛ばして** `human` へ行くので、振る舞いは今までどおり（PR を作って人間がマージする）。

```yaml
auto_merge: true            # 既定値で有効にする
auto_merge:                 # 値を選ぶなら dict で
  method: merge             # merge | squash | rebase（既定 merge）
  wait_min: 20              # CI checks を待つ上限（分）
  delete_branch: true       # マージ後に origin の作業ブランチを消す
  require_checks: true      # checks が 0 本ならマージしない（CI が無い PJ は false）
```

マージするのは次を**全部**満たすときだけ: `gates.txt` に `FAIL` が無い（`INFO`＝base でも赤は可）／workflow に reviewer が
居るなら `review.md` の 1 行目が `# レビュー: PASS`／PR が OPEN で draft でない／宛先と head がこの run のもの／
`gh pr checks` が全部 pass（30 秒ごとに `wait_min` まで。1 本でも fail なら止める）／`mergeable` が `MERGEABLE`。

満たさなければ**マージせず PR を開いたまま** `human` へ渡し、理由を `state.json` の `error` に `automerge: <理由>` の
1 行で残す（`resume_step` は付けない。PR はできていて、続きから回す対象ではない）。マージしたら `state.json` に
`merged: {at, sha, method, pr_url, base}` を残し、PR に自動マージのコメントを 1 つ付ける。kb はチケットを `done` にする。

`auto_merge` は**全 workflow に効く**。`hotfix_base: main` の PJ で真にすると hotfix が `main` へ自動で入る。

### agent step の時間上限（`timeout_min`）

| workflow | research | plan / design | implement | review | resolve |
|---|---|---|---|---|---|
| hotfix / bug / feature / chore / docs / merge-pr / research | 60 | 60 | 60 | 60 | 60 |
| feature-long | 40 | 40 | **180** | 60 | 60 |

既定は 60 分（`workflow/bin/run` の `DEFAULT_TIMEOUT_MIN`）。step ごとに yml の `timeout_min` で上書きする。

上限を超えると `timeout` が rc=124 で切る。runner はそれを普通の失敗と分けて扱い、**追跡済みの未コミット変更だけを
`wip: step timeout` としてコミット**してから（未追跡は足さない）人間に返すので、退避ブランチ `origin/sandbox/<id>-<wf>-wip`
に途中までの実装が残る。`state.json` の `error` は `implement: 時間上限 60 分で中断（timeout）` の 1 行、agent の
stdout の末尾は `last_output`、`history` の末尾に `failure: "timeout"` と `timeout_min` が入る（チケット 329）。
14 ファイル超の作業を 60 分で回すと切られるので、大きい実装は `feature-long` を使うかチケットを割る。

## 音声・チケットの入口

当面、音声メモからチケットは作らない（メンテナの判断 2026-09-06）。workflow は `ticket.md`（1 行目が題名、任意で 2 行目 `pr: N`）から始まる。チケットの置き場と採番は `../kanban/`（本文は `$AIFACTORY_WORKSPACE/kanban/tickets/<id>-<pj>-<slug>.md`。2026-09-06 に `workflow/tickets/` から移した）。種別の自動判定（ルーター）は glue。

## 実績

- 2026-09-06（夜）: Web コンソールから `kb run 206`（kumitate / research）。実機 VM で research 18 分（70 ターン、$1.78）+ judge 1 分。逐次ログ 25 KB（生 660 KB）。結論は `runs/2026-09-06-kumitate-206/work/summary.md`（ゲート差分実行は保留、`packages/db/.data/` の gitignore が先）
- 2026-09-06（夕）: agent / code step の出力を逐次書き込みに変更（stream-json を人が読める形へ。ADR-0014）。Mac 上の偽 VM（シム）で research を 2 周して確認
- 2026-09-06: v0（`spin-v0.sh`）で kumitate ほか 1 PJ の CLAUDE.md 整理と実バグ修正の 2 周を回した（kumitate #293 / #294 ほか）。v1 runner は同日に作成し、`research` と `merge-pr`（Rails PJ の PR 2 本を develop へマージ）で実機検証。詳細は `runs/` と `../sandbox/STATUS.md` の「1周の所見」

## 踏んだ罠と対処（2026-09-06）

| 罠 | 症状 | 対処 |
|---|---|---|
| base で既に赤いゲートを agent に「直せ」と戻した | docs だけの PR（kumitate #294）に、無関係なテスト修正が混入した（#293 と重複） | runner が赤いゲートを base でも回し、base でも赤ければ FAIL → INFO に格下げして戻さない（ADR-0038）。手で省きたいときは従来どおり `project.yml` の `known_red_gates` に書ける |
| テンプレートから base が進んで VM の環境がずれた | kumitate の実 DB テストが列不在で全滅し、3 attempt を「gates 赤の修正」に浪費（#288 / #290 / #293） | `project.yml` の `prepare` で貸出直後に依存と migration を base へ揃える。揃わなければ agent を起こさず `failure: prepare` で止める |
| `git add -A` の自動コミット | テストが生成した DB（turso の .db）を拾いかけた | 追跡済み変更だけ `git add -u`。未追跡は一覧を記録して push しない |
| 変数の直後に全角括弧（`$loop）`） | bash が全角まで変数名と見て「未定義」 | 常に `${var}` と書く。runner を Python にした理由の一つ |
| 実行中の bash スクリプトを編集した | 走っていた spin が途中で壊れた | 走っている run があるときはスクリプトを触らない。直すなら止めてから |
| 同じ日に同じチケットを再実行した | `runs/<date>-<pj>-<task>/` を上書きして 1 回目の記録が消えた（kumitate 203） | runner が既存の run ディレクトリを `-attemptN` に退避してから作る。`--resume` は退避しない |
| 別セッションが同時に sandbox を作り替えていた | firewall 設定で全 VM を再起動 → 貸出中の VM への SSH が切れ、run が `human` で終了 | 貸出中の VM を触る作業は `~/.config/sandbox/state.json` を直前に読んで飛ばす。run が VM 起因で落ちたら `kb reopen` → `kb run` で再実行 |
