# ADR 0038: 貸出直後の準備は project.yml の `prepare` で行い、「base でも赤いゲート」は runner が base で回して確かめる

- 状態: Accepted
- 日付: 2026-09-09

## 状況

sandbox VM は run が終わるたびにテンプレート（`provision.sh` を焼いた時点）へ戻る。テンプレートは 1 回焼いたら固定で、base ブランチだけが進んでいく。この「テンプレートと base のずれ」と「base 自身の赤」が、同じ症状（gates が赤 → implement へ戻す → 直らない → ループ上限で human）で観測された。

- **kumitate（環境のずれ）**: provision 時点の migration で DB が固定されていて、develop に 0088 / 0089 が入った後の run は `packages/db` の実 DB テストが列不在で全滅した。#288 / #290 で同時に、#293 でも再発し、計 3 attempt を「gates 赤の修正」に浪費した。暫定策として `gates.sh` の先頭に `gate db-migrate …` を足した（main b691756）が、これは「準備」をゲートの中に紛れ込ませたもので、PJ ごとに書き方が分かれる。
- **aifactory 自身（base 自身の赤）**: 2026-09-09 09:20 UTC、run 329 / 280 の `unittest-console` が赤。原因は main の e79dc77（`feature-long.yml` 新設）が `console/static/strings.js` の `kind.*` に説明を足しておらず、`test_kind_covers_every_workflow` が **base でも赤**だったこと。329 は gates → implement を 2 周（上限）消費した。こちらは準備では直らない。

どちらも実装役は「前回の結果（赤）を直せ」と依頼されるので、環境起因・base 起因の赤を自分の変更のせいだと思って時間を使う。役割文書には「base でも赤なら直さず report に書く」と書いてあったが、**base で回して確かめる手順を与えていなかった**ので、判断が推測になっていた。`project.yml` の `known_red_gates` は「FAIL → INFO に格下げして戻さない」を実装済みだが人が手で書く前提で、kumitate も aifactory も空のままだった。

## 決定

1. **貸出直後の準備は `project.yml` の `prepare: <名前>.sh`（任意）で宣言する。** runner は take の checkout 直後、最初の agent step の前に、`app_dir` を cwd にして VM で 1 回だけ実行する。`--from`（wip の続き）や PR 起点の run でも走らせる（続きであっても VM は素のテンプレートなので、環境は揃え直す）。
2. **準備が失敗したら agent を起動せずに終わる。** `state.json` に `result: failed` / `failure: "prepare"` を書き、工程は 1 つも始めない（`history: []`）。環境が整っていない VM で agent を起こしても、直せない赤を直そうとして VM 時間と費用を焼くだけなので、人へ返す。kb はチケットを `blocked` にする（`wait_timeout` のように todo へは戻さない。放っておいても直らない）。
3. **「base でも赤か」は `kit/steps/gates.sh` の内側で、赤いゲートだけを base で回し直して確かめる。** step は増やさない。HEAD で `FAIL` が出た名前だけを `origin/<base>` で実行し、base でも赤かったものは `INFO <名前> red (also red on base; not a gate)` に落とす。残りが無ければ gates は PASS として review へ進む（implement への戻しを消費しない）。
4. **base での確認は同じ作業コピーで checkout を差し替えて行う**（未コミットの変更は `git stash` で退避し、必ず戻す）。**別 worktree は使わない。**
5. **確かめた結果は run の記録（`state.json` の `known_red_gates`）に書き、`project.yml` は書き換えない。** console の `sandbox_status` は「`project.yml` に人が書いた値 ∪ 直近の run が確かめた値」を見せる。
6. **PJ の `gates.sh` は「引数があればその名前のゲートだけ走らせる」契約にする**（引数なしは従来どおり全部）。base で全ゲートを回し直すと gates の時間が 2 倍になるため。
7. **pull backend（macOS / Windows / Linux）は対象外。** あちらは `take` を独自に持ち、既存の `provision.sh` を毎 take 実行していて同じ役目を既に果たしている。名前とログ名の統一は別票にする。

## 理由

- **なぜ準備をゲートから分けるのか。** ゲートは「品質を測る」もので、測る前に環境を揃えるのは別の仕事。混ぜると、環境の失敗が「品質の赤」として実装役に戻り、まさに #288 / #290 / #293 で起きたことが再発する。失敗したときの正しい宛先も違う（ゲートの赤は実装役、準備の失敗は人）。
- **なぜ別 worktree を使わないのか。** 新しい worktree には追跡外の生成物（`node_modules` / `website/.venv` / ビルド結果）が無いので、base ではほぼ全ゲートが赤くなる。それを「base でも赤い」と読むと、**自分が壊した赤まで INFO に落ちる**。同じ作業コピーで checkout だけ差し替えれば、比べているのはコミットの中身の差だけになる。
- **なぜ `project.yml` に自動で書かないのか。** あれは人が書く設定ファイルで、機械が書き換えると run の途中で PJ 定義が変わる。`known_red_gates` を人が消し忘れると赤を恒久的に隠すが、run の記録は「その run の時点でそうだった」という事実として腐らない。
- **なぜ ADR-0032 を書き換えないのか。** ADR-0032 決定 5 は「base は base のゲートで緑である前提に乗る」と書いている。この 2 事例はその前提が崩れる場合で、前提の側を更新する。既存 ADR は書き換えない約束（README）。

## 結果（トレードオフ）

- 赤が出た run では gates の時間が伸びる（赤いゲートの分だけ、最大でもう 1 回）。全緑の run では 1 秒も増えない（赤が無ければ base を見に行かない）。重い PJ は `known_red_gates` を手で書けば base の再実行を省ける（既存の経路をそのまま残す理由）。
- 同じゲート名に複数のテストが入っていると、**base の赤に隠れて自分が壊した分が INFO に落ちる**可能性がある。そのため `gates.txt` に base 側の結果とログ末尾を `=== base check:` 以降に残し、reviewer と人間が確かめられるようにする（review の入力は変えない）。ゲートを細かく分けている PJ ほどこの穴は小さい。
- `git stash` は追跡外のファイルも動かす（`-u`）。`.gitignore` されていない大きな生成物がある PJ では遅くなる。base を見た後に作業ブランチへ戻し切れなかったときは、壊れた作業ツリーで agent を走らせず `human` へ回す。
- `prepare` を書いた PJ は、VM を取ってから最初の agent が動くまでが `prepare` の実行時間だけ延びる（kumitate で `pnpm install` + migration）。それでも、環境起因の赤に 1 attempt（数十分）を使うより安い。
