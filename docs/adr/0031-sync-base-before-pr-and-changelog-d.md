# ADR 0031: PR を作る直前に base を取り込む／`CHANGELOG` は `changelog.d` に分ける

- 状態: Accepted
- 日付: 2026-09-08

## 状況

runner は `take` の時点で `origin/<base>` を fetch して作業ブランチを切り、その後 base を一度も見ない（`workflow/bin/run` の `take()`。`kit/steps/pr-create.sh` は `git log origin/$BASE..HEAD` と push だけ）。衝突を検出する場所が GitHub 側にしか無い。

そのため、並列に走った別 run の PR を先にマージすると、後発の PR は **必ず** CONFLICTING になる。2026-09-08 に aifactory の UIUX チケット 13 件を 2 並列で回したところ、7 本（PR #5, #6, #8, #9, #11, #12, #14）がこれで止まった。衝突箇所はほぼ決まっている:

- `CHANGELOG.md` の `## [Unreleased]` 直下（全 run が同じ行に箇条書きを足す）
- テストファイルの同じ位置へのテスト追加、ガイドの表の同じ行、関数の隣接行

救済は `merge-pr` workflow（VM を 1 台借りて implementer が解消 → gates → review → merge）だけで、1 本 15〜20 分。機械で片付く種類の衝突が人手に落ち、PM が手元で「両方残す」を 7 回やった。

さらに、`docs/adr/` の採番は **別ファイル**なので git は衝突と見なさない。235（PR #17）と 230（PR #18）が同時に走って両方 `0026-*.md` を採番し、自動マージが「成功」した。`docs/adr/README.md` の一覧だけが衝突して、かろうじて気づけた。

## 決定

1. **PR の直前に base を取り込む step（`sync`）を入れる。** 全 workflow（bug / feature / hotfix / docs / chore）の `pr` の直前に `code: sync-base` を置く。中身は `git fetch origin <base>` →（`origin/<base>` が HEAD の祖先なら取り込み済み）→ `git merge --no-edit origin/<base>`。
2. **衝突は人間ではなく implementer に戻す。** `sync` の `on_fail` は `{ goto: resolve, max_loops: 2, else: human }`。`resolve` は implementer の step で、`merge-pr` の resolve と同じ仕事（両方の意図を残す解消）をし、`next: gates` で戻る。人間に落ちるのは 3 回目の失敗だけ。
3. **衝突したツリーを agent に渡さない。** `sync` は衝突を検出したら `git diff --name-only --diff-filter=U` でファイル名を控えてから `git merge --abort` する。implementer は綺麗な作業ツリーから自分で `git merge` をやり直す。
4. **取り込みの後に ADR 番号の重複を検査する。** `docs/adr/` の `NNNN-` が 2 つ以上あれば `sync` を FAIL にし、「次の空き番号に振り直し、参照も更新」を implementer に戻す。planner は ADR 番号を計画に固定せず、書く直前に `ls docs/adr/` で取り直す。
5. **衝突が無ければ gates を回し直さない。** `sync` → `pr` に直行する。
6. **`sync` は runner 内蔵にする**（`kit/steps/` にファイルを置かない）。pull backend は code step を `gates.sh` / `pr-create.sh` に限定しているので、bash と PowerShell の 2 実装に分かれるのを避け、全 backend 共通の `self.sb()` で 1 実装にする。Windows backend だけは PowerShell の `sb` なので PASS 扱いで飛ばす（実機で検証できないため）。
7. **`CHANGELOG.md` の衝突は構造で無くす。** 各 run は `CHANGELOG.md` を編集せず `changelog.d/<チケット番号>-<slug>.md`（1 ファイル 1 項目、`### Added` などの見出しつき）を置く。リリース時に `bin/changelog-release collect` が `## [Unreleased]` へ見出しごとに集約して元ファイルを消す。`bin/changelog-release check --base <ref>` が「`CHANGELOG.md` を触っていて、かつ `changelog.d/*` の削除を伴わない」を非 0 にし、PJ のゲートで見る。
8. **人間へ渡す理由を記録に残す。** `sync` を含むどの step でも、失敗して行き先が `human` になったときは `state.json` の `error` に「どの step で・なぜ」を書き、`kb` はその末尾行をチケットの note に添える。

## 理由

- 衝突を「PR を出した後の GitHub の状態」ではなく「PR を出す前の自分の作業ツリー」で見つければ、解消できる主体（VM を持っている implementer）がその場に居る。VM を借り直す `merge-pr` の 15〜20 分が要らない。
- 衝突の大半は「両方残す」で済む機械的なもの。人間の判断が要るのは、それを 2 回試して駄目だったときだけ。
- `CHANGELOG` は「必ず同じ 1 行に足す」という構造そのものが原因なので、取り込みを入れても毎回 implementer に戻る。ファイルを分ければ git のマージ単位が分かれ、そもそも衝突しない。
- ADR の採番は git から見れば衝突ではない（別ファイル）。人間が README の一覧で気づくまで見えないので、機械が番号を数えるしかない。
- gates を回し直さないのは、`sync` の後の差分が「自分の変更 + base」だから。base 自身は base のゲートで緑である前提に乗る。

## 結果（トレードオフ）

- **base 側の変更と意味的に衝突しても PR は出る。** テキストとして衝突しなければ `sync` は PASS し、gates は回し直さない。これはチケットの方針どおり受け入れる。壊れていれば PR のレビューか base の CI が拾う。
- `sync` の後に gates が赤くなる経路（`resolve` → `gates`）は既存の `gates → implement`（上限 2）に乗る。`resolve` 専用のループは作らない。
- Windows worker では従来どおり base を取り込まない。PowerShell 版を書くのは実機が無いので保留。
- `bin/changelog-release check` は `origin/<base>` が取れる場所（VM のゲート）でだけ働く。shallow clone の CI には入れない（取れなければ検査を飛ばして 0）。
- 既に `## [Unreleased]` にある項目は移さない。次のリリースで `changelog.d` の分と一緒に閉じる。
- `human` の判定は `result == "human"` では通常終了（bug も `pr` の後 `human`）と区別できない。区別は `next` / `error` / `pr_url` の有無で行う。
