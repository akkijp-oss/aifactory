# ADR 0036: 止まった run の再開は「新しい VM で、wip ブランチの続きから、指定の step」で行う

- 状態: Accepted
- 日付: 2026-09-09

## 状況

`feature` の review は `on_fail: { goto: implement, max_loops: 1, else: human }` で、2 回目の FAIL が**軽微な 1 件**でも run は `human` で終わり、VM は返却される。

このとき、続きから回す口が無かった。既存の `--resume` は `Run.take()` が `self.resume` のとき丸ごと `return` する（`sandbox take` も checkout もしない）＝**貸出中の同じ VM が前提**で、返却後は使えない。残された道は `kb run` のやり直しだけで、research / design から全部走り直す（時間と費用の二重払い）。

一方、続きを回すのに要る材料は #329 で既に揃っていた。`preserve()` が `sandbox/<task>-<workflow>-wip` に HEAD を force push し、`state.json` に `wip_branch` / `error` / `failure` / `last_output` を残す。前回の `work/plan.md` `research.md` `review.md` も run ディレクトリに回収されている。足りないのは「新しい VM を取り、wip から checkout し、指定の step から始め、前回の成果物と指摘を持ち込む」経路と、その導線（人が打つコマンドの見せ方）だけだった。

実際 kumitate #292 / #255 / #265 / #279 では、PM が wip ブランチを worktree に取り出して手で直している（4 回）。

## 決定

1. **再開は `--resume` とは別の経路にする。** `workflow/bin/run` に `--from[=<step>]` / `--branch=<名前>` を足す。この経路は通常起動（`resume=False`）と同じく VM を新しく take し、run ディレクトリも新しく作る。既存の `--resume`（貸出中の VM で `state.json` の次の step から）の分岐には手を入れない。両方を同時に指定したら止める。
2. **前回の run は環境変数 `AIFACTORY_FROM_RUN` で渡し、run 名の形（`<日付>-<pj>-<task>`）しか受け付けない。** `--resume` の `AIFACTORY_RESUME_RUN` と同じガードで、他チケットの記録や任意のパスを読ませない。前回が「今日の同じ名前の run」なら、退避先（`-attemptN`）を前回として読む。
3. **state.json に足すのは事実だけ。** 止まった側に `resume_step`（次にやり直す step。失敗した step の `on_fail.goto`、無ければその step 自身）、再開した側に `resumed_from` / `from_step` / `from_branch`。**再開コマンドの文字列は書かない**（ADR-0025 の「表示の文言は console 側で導く」を守る。組み立てるのは `console/lib/core.py` の `resume_command()` と `kb` の note）。
4. **前回の `work/*.md` `*.txt` は新しい VM に持ち込む**（`ticket.md` / `prompt.md` を除く）。research / design を回さずに `plan.md` を入力に取れるようにするため。加えて、最初の step の依頼文に前回の `review.md` を「前回の結果（直すこと）」として入れる（`review` からやり直すときは入れない）。
5. **`loops` は空から数え直す。** 再開は新しい run なので、`review → implement` の戻せる回数はもう一度使える。
6. **人が打つ値（step 名・ブランチ名）は使う前に検査する。** step は workflow に在ること、ブランチ名は `[A-Za-z0-9][A-Za-z0-9._/-]*` で `..` を含まず `/` `.lock` で終わらないこと。使う所では `shlex.quote` する（外部入力が初めてシェルに乗る経路）。
7. **対象は sandbox backend だけ。** pull backend（macOS / Windows / Linux）は `take()` を独自に持ち、wip からの checkout をしないので `--from` は拒否する。

## 理由

- **なぜ `--resume` を拡張しないのか。** 「貸出中の VM で続ける」と「新しい VM を取り直して続ける」は、VM の取得・checkout・run ディレクトリの扱いが全部違う。同じフラグに 2 つの意味を持たせると `take()` の分岐が読めなくなる。フラグを分ければ、既存の `--resume` の挙動は 1 行も変わらない。
- **なぜ run を新しく作るのか。** 前回の記録（ログ・依頼文・成果物）は「なぜ止まったか」の証拠で、上書きすると人が読めなくなる。同日の再実行を `-attemptN` に退避する既存の方針（2026-09-06）と同じ考え方。
- **なぜ state.json にコマンドの文言を書かないのか。** ADR-0025 の決定。記録の形式は読み手（console / kb / MCP）より寿命が長く、文言を混ぜると表示を直すたびに記録の書式が変わる。#329 が `error` などの**事実**を足したのと同じ線引きにする。
- **なぜ severity（軽微な FAIL なら もう 1 周）を入れないのか。** reviewer の出力書式（1 行目の PASS / FAIL）は 5 つの workflow が共有する契約で、変えると `run_agent` のパースと `transition()` にも波及する。再開が 1 コマンドで済むなら軽微な FAIL の被害はほぼ消えるので、まず本命だけ入れる（別票）。

## 結果（トレードオフ）

- wip ブランチ名は task + workflow 固定（#329）なので、**同じチケットを 2 つ同時に再開してはいけない**（後から `preserve()` した方が上書きする）。再開 run は旧 wip を含んでいるので、直列に回すぶんには失うものは無い。
- 再開した run の PR は普段どおりの名前のブランチから出る（wip は保全専用のまま）。
- `resume_step` の無い古い run は `history` の最後の step に落ちる。それも決まらなければ止めて `--from <step>` を要求する（推し測って research から回し直さない）。
- 再開の口が増えたぶん、`kb run` のフラグは 1 つ増える（`--from` / `--branch`）。console の run 画面には 1 行（打つべきコマンド）だけを出し、ボタンは作らない。
