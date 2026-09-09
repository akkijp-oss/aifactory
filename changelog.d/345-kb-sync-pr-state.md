### Added
- **kb: `kb sync` が GitHub の PR の状態を見て、レビュー待ちのチケットを自動で片付けるようになった**。`review` で PR 番号が
  入っているチケットについて `gh pr view` を呼び、PR が **MERGED なら `done`**（メモの 1 行目に `PR #n マージ済み <日時>`）、
  **マージされずに CLOSED なら `blocked`**（`PR #n がマージされずに閉じられた <日時>`）にします。`OPEN` は何も変えません。
  人が書いたメモの 2 行目以降はそのまま残ります（ADR-0048）
- **kb: `kb sync --all-review [--pj P]` を足した**。`review` のチケット全件の PR をまとめて見て、最後に
  `対象 5 件 / done 2 / blocked 1 / 変更なし 2 / 飛ばした 0` の 1 行を出します。`--dry-run` で下見できます
- **dispatch: 回し始める前に `kb sync --all-review` を 1 回呼ぶようになった**。GitHub でマージした PR が次の配車で板に反映され、
  `dispatch.log` に結果が 1 行残ります（`--dry-run` / `--resume-paused` では呼びません）

### Changed
- **kb: `kb sync <id>` が run に紐づいていないチケットでも動くようになった**（`review` かつ PR 番号があれば PR だけを見ます）。
  run も PR も無いときは今までどおり断ります
- `gh` の認証は PR 作成と同じ PJ 別の GitHub App（ADR-0008 / ADR-0030）を使います。App も `GH_TOKEN` も無い環境
  （開発機・CI）では、チケットを変えず終了コード 0 のまま、理由を `[kb] warn:` として標準エラーに 1 行出して飛ばします（ADR-0050）
