# ADR 0053: 軽微な FAIL は `severity: minor` で 1 周だけ延長する。再開に渡す review.md は FAIL のときだけ、二重再開は断る

- 状態: Accepted（ADR-0036 が「別票」と名指しした 3 点。ADR-0036 の決定は変えない）
- 日付: 2026-09-11

## 状況

ADR-0036 で `kb run <id> --from <step> --branch <wip>`（新しい VM で wip の続きから）が入り、止まった run を 1 コマンドで再開できるようになった。そのとき次の 3 点は「別票」として残した。運用してみて、3 つとも実害が出た。

1. **軽微な FAIL でも human で終わる。** review は 5 つの workflow すべてで `on_fail: { goto: implement, max_loops: 1, else: human }`。2 回目の FAIL が「テストが 1 件足りない」でも run は止まり、VM は返却される。再開が 1 コマンドで済むようになっても、人が板を見て打つまでの待ち時間は消えない。reviewer には「この FAIL は軽いのか重いのか」を書く箱が無く、`transition()` は `(step, ok)` しか見ていない。
2. **PASS した review.md が「直すこと」として次の run に渡る。** `first_retry_note()` は前回の `work/review.md` に中身があれば無条件に「レビュー指摘（直すこと）」として implementer の依頼文に入れる。review が PASS したあとの step（`sync` の衝突 → `resolve`、`pr`）で止まった run を `--from` で再開すると、**合格の判定文**（「計画の範囲に収まっている。ゲートも全緑」）を指摘として渡してしまう。implementer は直すものが無いのに直しに行く。
3. **同じチケットを 2 本同時に再開できてしまう。** wip ブランチ名は task + workflow 固定なので、後から `preserve()` した run が先の run の wip を上書きする。ADR-0036 の「結果」に制約として書いたが、機械では何も止めていなかった。`kb run --from` は台帳の状態を一切見ない。

## 決定

1. **reviewer は FAIL のときに `severity: minor|major` を 1 行書く。** 置き場は **2 行目以降の独立した 1 行**。1 行目の `# レビュー: PASS|FAIL` は 5 つの workflow と `pr-automerge.sh`（ADR-0042）が共有する契約なので**変えない**。読むのは `workflow/bin/run` の module 関数 `review_severity()`（1 行目は見ない＝`# レビュー: PASS` を severity と誤読しない）。判定そのものは `review_verdict()` に切り出し、`run_agent()` と `first_retry_note()` の両方が同じ 1 か所を使う。
2. **`severity: minor` の FAIL に限り、戻せる回数の上限を 1 回だけ超えて `goto` に戻す。** 加点は **1 遷移につき 1 回きり**（`state.json` の `severity_bonus: {"<from>-><to>": 1}` に事実だけを残す。文言は書かない＝ADR-0025）。何度 minor と書かれても増えない。`major` と無指定は従来どおり `else`（human）。加点するのは**上限を使い切ったときだけ**で、上限内なら加点を温存する。reviewer 以外の role の step（`gates` の戻しなど）は対象外。`workflow/kit/workflows/*.yml` の `max_loops` は変えない。
3. **再開の依頼文に前回の review.md を入れるのは、1 行目が FAIL のときだけ。** 既存の「`review` からやり直すときは入れない」はそのまま。PASS だったときは、これまでどおり `state.json` の `error` の最終行 1 行（止まった理由）を添える分岐に落ちる。チケットは `resolve` / `pr` のときと限定していたが、PASS の review.md が残る状況は reviewer 以外の step からの再開全般で起こるので**一般化する**。
4. **台帳が `in_progress` で、その `run` の記録も終わっていない（`state.json` に `finished` が無い、または記録がまだ無い）ときは `kb run --from` を断る。** `--force` を付けたときだけ通す。判定は `kb` の `run_in_progress()` 1 か所（`human_note()` と同じ見方）。`--dry-run` では判定しない。断り方は「どの run が実行中か」と「`kb sync` で台帳を合わせるか `--force`」が分かる 1〜2 行（表示は kb 側＝ADR-0025）。

## 理由

- **なぜ上限 +1 固定で、`max_loops` を増やさないのか。** `max_loops` を 2 にすると、重い FAIL でも 2 周する。ここで縮めたいのは「軽微な 1 件のために人を待たせる時間」だけで、「直らないものを回し続ける時間」ではない。reviewer が軽いと判断したときに限り、しかも 1 回だけ延ばす、が最小の変更になる。
- **なぜ加点を「1 遷移につき 1 回」に固定するのか。** 加点のたびに上限が上がると、reviewer が毎回 minor と書けば run が止まらない。`severity_bonus` を「使ったか」の目印にして、2 回目以降は加点済みの上限をそのまま使う（＝増えない）。テストで上限が `max_loops + 1` を超えないことを固定した。
- **なぜ severity を 1 行目に混ぜないのか。** 1 行目は `run_agent()` のパース・`pr-automerge.sh`・5 つの workflow が共有する契約で、ADR-0036 が「変えると波及する」と書いた当のもの。2 行目以降に独立した 1 行として足せば、既存の読み手は 1 行も変わらない。
- **なぜ severity を `transition()` の引数ではなく `self.last_severity` に置くのか。** `transition()` の呼び元は `main()` の 1 か所だが、review.md の本文はそこに届いていない（`run_agent()` の中で読む）。`self.last_fail` / `self.base_check_broken` が既に「直前の step の事実を次の判断に渡す」同じ流儀で置かれているので、それに合わせる。ループの先頭で毎回リセットし、前の review の severity を次の step に持ち越さない。
- **なぜ PASS の review.md を渡さないのか。** 依頼文の見出しが「前回の結果（直すこと）」である以上、そこに入る文章は直す対象でなければならない。合格の判定文を入れると、implementer は直すものを探して範囲外に手を出す（この PJ で最も避けたい失敗）。判定を機械で読めるようにした（決定 1）ので、条件は 1 行で足りる。
- **なぜ二重再開を台帳の `status` だけで判定しないのか。** `kb sync` を忘れた台帳は `in_progress` のまま古くなる。それだけで断ると「終わっている run のせいで再開できない」が起きる。逆に記録だけで判定すると、板が `todo` に戻された利用枠切れの run（`finished` 済み）まで見に行くことになる。両方が「まだ動いている」と言っているときだけ断る。`--dry-run` は VM も wip も触らないので判定しない。
- **なぜ既定を拒否側にするのか。** 上書きされるのは前の run が push した wip ブランチ＝そこまでの作業そのもの。取り返すには reflog が要る。打ち直しは 1 秒、失った wip は戻らない。

## 結果（トレードオフ）

- reviewer が severity を書き忘れた FAIL は従来どおり human で終わる（既定が安全側）。書式の追加なので、古い review.md（severity 行なし）もそのまま読める。
- reviewer が軽くしたい気持ちで minor と書くと、直らない指摘で 1 周ぶん余計に回る。上限が +1 で固定なので、被害は 1 周（＝implement + gates + review）に収まる。
- `severity_bonus` を持たない古い `state.json`（`--resume` で読み直した記録）でも落ちない（`.setdefault` で読む）。console の `loops_hit`（`loops >= max_loops`）はそのままで、加点した回は「使い切った」と表示される。表示の追随は今回の範囲外。
- `--force` は `kb` にだけ足す。MCP の `ticket_run` には出さない（MCP は同じチケットのジョブ重複を別に見ている）。MCP から二重再開したくなったら別票。
- `--from` を打ったときだけ効くガードなので、`--from` を使わない通常の `kb run` と `--resume` の経路は 1 行も変わらない。
