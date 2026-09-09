# ADR 0050: 「PR がどうなったか」は GitHub を正本に kb sync が見る（gh が使えなければ黙って飛ばす）

- 状態: Accepted
- 日付: 2026-09-10
- チケット: 345

## 状況

`kb sync`（= MCP の `ticket_action sync`）は `runs/<run>/state.json` を読み直すだけで、GitHub を一度も見ていなかった。
runner が自分でマージした run は `state.json` の `merged` から `done` になる（ADR-0042）が、**人が GitHub の画面で
マージした PR** は runner の記録に何も残らないので、チケットは `review` / メモ `[run] PR 待ち <url>` のまま残る。

2026-09-09 に asura で 13 件（#11, #13〜#25）をマージしたときは、1 件ごとに `gh pr merge` →
`kb done` → `kb set --note "PR #n を develop にマージ…"` を手で打った。「PR が MERGED = そのチケットは完了」は
ほぼ常に真なので、機械で拾える。

一方で制御系（VM の外）から `gh` を叩くには PJ 別の GitHub App からトークンを払い出す必要があり（ADR-0008 /
ADR-0030）、その仕組みは `workflow/bin/run` の `ensure_gh_token()` 1 か所にしか無かった。開発機・CI・この sandbox VM には
App の秘密鍵も `sandbox` コマンドも無い（`sandbox/` は Proxmox ホスト側の運用物）ので、kb がトークンを前提にすると
テストも普段の `kb` も動かなくなる。

## 決定

1. **PR の今の状態は GitHub を正本にし、`kb sync` が `gh pr view <n> -R <repo> --json state,mergedAt,closedAt,mergeCommit`
   で確かめる。** 対象は `status = review` かつ `pr` が入っているチケットだけ。
2. **判定は `state` と `mergedAt` の 2 つだけを見る。**
   - `MERGED` → `done`、メモの 1 行目に `PR #n マージ済み <mergedAt>`
   - `CLOSED` かつ `mergedAt` が空 → `blocked`、メモの 1 行目に `PR #n がマージされずに閉じられた <closedAt>。作り直すなら kb reopen → kb run`
   - `OPEN`・こちらが知らない状態・問い合わせの失敗 → **何もしない**（DB を一切触らない）

   誤って `done` にするのが最大の害なので、不明はすべて「何もしない」に倒す。時刻は GitHub が返した値（`Z` 付き）を
   そのまま書き、こちらで時間帯を推し量らない。
3. **`gh` が使えない環境では黙って飛ばす。** 「黙って」の定義は *チケットを変えない・終了コードは 0・標準出力には出さない・
   理由を `[kb] warn: …` として標準エラーに 1 行*。`--all-review` では PJ ごとに 1 行だけ出す。
   これで App の無い開発機・CI・sandbox VM でも `kb sync` と `dispatch` は今までどおり成功で終わる。
4. **`gh` のヘルパーは `lib/aifactory_gh.py` に 1 か所**（`scrub` / `ensure_gh_token` / `pr_state`）。
   `workflow/bin/run` はそこへの薄い委譲にし、kb も同じものを使う。トークンの値はログにも記録にも戻り値にも出さない。
   App のトークンは PJ のリポジトリ限定なので、`--all-review` で PJ を跨ぐときは **PJ ごとに払い出し直す**
   （env に最初からある `GH_TOKEN` は今までどおり全 PJ に使う）。
5. **`kb sync --all-review [--pj P]` を足し、`dispatch` は回し始める前に 1 回だけ呼ぶ。**
   `--dry-run`（状態を進めない約束）と `--resume-paused`（5 分ごとの timer。GitHub を叩き続けない）では呼ばない。
6. **PR 由来の更新でも run 記録への転記（`transcribe_human`。ADR-0039）はしない。** 「sync は転記しない」という
   既存の規約を変えない。人の後始末の転記が要るなら別の口（`kb done` / `kb set --pr`）を通す。

## 検討して採らなかった案

- **GitHub の webhook を受ける。** 反映は速いが、制御系に公開エンドポイントと署名検証が要る。今は `dispatch` の
  起動時と PM の手打ちで足りる（レビュー待ちは 1 日に数件）。
- **`gh` が使えないときにエラーで落とす。** App の無い開発機・CI で `kb sync` と `dispatch` が全部赤くなる。
  PR の状態は「取れたら使う」補助的な入力で、取れないこと自体は異常ではない。
- **`ensure_gh_token` を kb に複製する。** 払い出しの挙動が 2 か所に分かれると、片方だけ直す事故が起きる。
  委譲で既存テスト（`workflow/tests/test_gh_token_from_app.py`）が緑のままだったので共通化した。
- **`mergeCommit` の有無でマージを判定する。** GitHub の反映が遅れて空になることがある（ADR-0042 で同じ理由から
  `merged.at` を見ると決めている）。`state` と `mergedAt` の方が確か。
- **`--all-review` を並列に回す。** 常用で 10 件を超えないので、1 件 20 秒の timeout を付けた直列で足りる。
  件数が増えたら上限オプションを別に足す。

## 影響

- `kb sync <id>` は run が無くても（`review` かつ `pr` があれば）動くようになる。run も PR も無いときだけ従来どおり断る。
- `kb sync <id>` に run がある場合は、`apply_result`（run 由来）を先に、GitHub 由来を後に適用する。
  GitHub の事実が `[run] PR 待ち …` を上書きする。
- メモの文言（`PR #n マージ済み …` / `PR #n がマージされずに閉じられた …`）は `run_note()` を通すので、
  人が書いた 2 行目以降は残る（ADR-0048）。
- 一覧・console・MCP は kb の出力を読むだけなので変更は要らない。
