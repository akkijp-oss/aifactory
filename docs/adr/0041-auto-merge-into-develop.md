# ADR 0041: PR は `base_branch`（aifactory は `develop`）宛てに作り、条件を満たせば runner がマージする。main への昇格は人間

- 状態: Accepted
- 日付: 2026-09-09

## 状況

全 workflow（bug / feature / feature-long / hotfix / docs / chore）は `pr` step で PR を作り `human` で終わる。機械がマージするのは既存 PR を直す `merge-pr` workflow の `pr-merge.sh` だけで、そこも `mergeable` しか見ていない（CI の合否は見ない）。

- 2026-09-09 の運転では PM が 10 本の PR を手でマージした。1 本あたり「CI 7 本の pass を待つ → `gh pr merge --merge --delete-branch`」で 3〜5 分。判断の中身は毎回同じ（ゲート緑・レビュー PASS・CI 緑）で、人が読んで決めていることは無い。
- 機械がマージする以上、入れる先が `main` のままだと「壊れた変更が既定ブランチに入る」余地が常に残る。
- GitHub ネイティブの auto-merge（`gh pr merge --auto`）は使えない。repo 設定が `allow_auto_merge: false` で、GitHub App の権限は Contents / Pull requests の write と Metadata の read だけ（ADR-0008）。設定変更にも branch protection の確認にも Administration 権限が要り、`gh api .../branches/main/protection` は 403 で読めない。

## 決定

1. **PR の宛先は今までどおり `project.yml` の `base_branch`。aifactory はそれを `develop` にする。** `develop` は `main` から生やした統合ブランチで、自動マージが入るのはここ。`main` は人間が `develop` から昇格させる安定版とする。他 PJ の宛先は変えない。
2. **`project.yml` に `auto_merge` を足す（任意・既定は無し）。** `true` か `{method, wait_min, delete_branch, require_checks}` の dict。無い PJ の振る舞いは今までと 1 ミリも変わらない（PR を作って人間に渡す）。
3. **`pr` の後ろに `automerge` step（`kit/steps/pr-automerge.sh`）を足す。** `auto_merge` が無い PJ では runner が **step ごと飛ばして** `human` へ行く（workflow の定義は全 PJ 共通なので、飛ばす判断は PJ の設定を持つ runner に置く）。
4. **マージする条件は全部満たしたときだけ**: `gates.txt` に `FAIL` が無い（`INFO`＝base でも赤は可。ADR-0038）／workflow に reviewer が居るなら `review.md` の 1 行目が `# レビュー: PASS`／PR が OPEN で draft でない／宛先と head がこの run のもの／`gh pr checks` が全部 pass（`wait_min` まで 30 秒ごとに見る。1 本でも fail・cancel なら止める。0 本は `require_checks` 次第）／`mergeable` が `MERGEABLE`。
5. **満たさないのは失敗ではなく、正常な終わり方の 1 つ。** マージせず PR を開いたまま `human` へ渡し、理由を `state.json` の `error` に `automerge: <理由>` の 1 行で残す。`resume_step` は付けない（PR はできていて、続きから回す対象ではない）。
6. **マージしたら runner が `state.json` に `merged: {at, sha, method, pr_url, base}` を足す。** 書き手は runner だけ（ADR-0039 の契約）。`result` の語彙は増やさず（`end` のまま）追加フィールドにする。kb はこれを読んでチケットを `done` にし、console の `run_outcome()` は `reason: "merged"` を導く（ADR-0025）。PR には「aifactory が自動マージした（run 名・条件）」のコメントを 1 つ付ける。
7. **`main` への昇格は機械がしない。** 手順は `sandbox/OPERATIONS.md` の「develop → main の昇格（人間）」に置く（`gh pr create --base main --head develop` → checks 確認 → `gh pr merge`）。`bin/ctl-update` は `origin/main` 追随のまま変えない。
8. **`.github/workflows/ci.yml` の `push.branches` に `develop` を足す。** 自動マージ後の `develop` にも CI を回し、統合ブランチが緑であることを見えるようにする。

## 理由

- **なぜネイティブ auto-merge を使わないのか。** 使うには repo 設定と branch protection を触る必要があり、どちらも今の GitHub App の権限では読むことすらできない。権限を足すのは別の判断（App の権限拡大）で、この票の範囲を超える。自前のポーリングなら Contents / Pull requests の write だけで足りる。
- **なぜ `develop` を挟むのか。** 機械が押すマージの入り先と、人が「これで動く」と保証する安定版を分けるため。既定ブランチが常に人の目を通っている状態を保ったまま、日々のマージ待ちだけを無くせる。
- **なぜ `result` に新しい値を作らないのか。** `result` は kb と console の分岐の土台で、語彙を増やすと読む側を全部直すことになる。ADR-0025 / ADR-0039 の方針（runner は事実を足し、読み方は導く側で決める）に沿って追加フィールドにした。
- **なぜ「checks 0 本」を既定で不合格にするのか。** 0 本は「CI が全部通った」ではなく「CI が動いていない」ことのほうが多い（ワークフローの構文エラー、トリガーの設定漏れ）。緑と見分けが付かないまま入れるより止めるほうが安い。CI を持たない PJ は `require_checks: false` で明示的に許す。
- **なぜ `--delete-branch` を `gh pr merge` に付けないのか。** VM は作業ブランチを checkout したままで、`gh` がローカルの checkout を切り替えようとして失敗しうる。消すのは `git push origin --delete` で別に行い、失敗しても run は止めない（残ったブランチは害が無い）。
- **なぜ待ち時間に上限を置くのか。** pending のまま待ち続けると VM を掴んだまま run が終わらない。上限（既定 20 分）で切って人間に渡すほうが、VM も判断も詰まらない。

## 影響

- `auto_merge` は **全 workflow に効く**。`hotfix_base: main` の PJ で `auto_merge` を真にすると hotfix が `main` へ自動で入る（aifactory は `hotfix_base` 未設定なので今は起きない）。
- 自動マージした run では、そのチケットは `done` になり人間の後始末（ADR-0039 の `human`）を待たない。
- `feature-long` は `pr` の後の行き先が書かれておらず `end` で終わっていた。`automerge` を足した結果、マージしなかったときは他の workflow と同じく `human` で終わる。
- 自動マージの直後は `develop` の CI がまだ走っていない期間がある。ADR-0038 の「base でも赤か」の確認は `origin/<base>` をその場で回す方式なので、CI の実行とは無関係に働く。
