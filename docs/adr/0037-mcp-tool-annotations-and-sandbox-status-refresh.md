# ADR 0037: MCP のツールに annotations を付ける／`sandbox_status` は古ければ裏で `sandbox ls` を起こす

- 状態: Accepted（ADR-0015 / ADR-0028 を補う。既存の決定は変えない）
- 日付: 2026-09-09

## 状況

PM が MCP だけで run を運転しようとして、結局 `ssh` と `~/.config/sandbox/state.json` の直読みに逃げた（チケット 336、2026-09-08 の実測）。理由は 3 つに分かれた。

1. `job_wait` を待つ間、同じセッションの `ticket_show` が返らなかった。サーバー側は ADR-0028 で既に別スレッドに逃がしてあり、テストでも他ツールは 4 秒以内に返っていた。残っていたのは呼び手の側で、Claude Code は `annotations.readOnlyHint` の無いツールを「並列に呼べない」とみなし、同じターンの呼び出しを直列に送る。`tools/list` は annotations を 1 つも返していなかった。
2. `sandbox_status` の `pool_actual` / `free` は「最後に成功した `sandbox ls` の値」で、更新は手で `sandbox_ls`（非同期ジョブ）を叩くしかない。28 時間前の値に `ls_stale: true` が付いたまま返り、増設した 3 台が見えなかった。空きの判断を誤ると `ticket_run` が「空きなし」で即失敗する。
3. `lent` が `{}` のことがあった。台帳の場所が `~/.config/sandbox/state.json` 固定で、`SANDBOX_STATE` / `SB_TENANT` を使うテナント運用では CLI と別のファイルを読む。しかも「台帳が無い」と「貸出が無い」がどちらも `{}` で、貸出先の IP は一覧の形で出ていなかった。

## 決定

1. **`tools/list` は全ツールに `annotations` を返す。** `title` と `readOnlyHint` は必須、取り返しのつかないもの（`sandbox_release` / `job_stop` / `computer_close`）に `destructiveHint: true`。表に無いツールは安全側（`readOnlyHint: false`）に倒す。MCP 2025-03-26 以降の任意項目なので、読まないクライアントは今までどおり動く。
2. **`sandbox_status` は `sandbox ls` が古ければ裏で取り直す。** 600 秒（`LS_STALE_S`）より古い、または一度も取れていないとき、`sandbox ls` のジョブを起こして **今回は古い値のまま** `ls_refreshing: true` と `ls_refresh_job` を付けて返す。次の呼び出しで最新になる。ssh に数秒かかるので、その場で待たせない。
3. **起こす条件は 3 つに絞る。** (a) 値が古い、(b) `sandbox-ls` が実行中でない（実行中ならその id を返すだけ）、(c) 直近の `sandbox-ls`（成否問わず）の開始から 600 秒経っている。`sandbox` が PATH に無い・二重起動になったなど起こせないときは `ls_refresh_error` を添え、`sandbox_status` 自体は成功で返す（読めた分は返す）。
4. **貸出台帳の場所は環境変数 `SANDBOX_STATE` が正。** `glue/bin/dispatch` と `workflow/bin/run` と同じ規則に揃える。`~/.config/aifactory/ctl.env` に書いてあれば、それを読んだあとで決め直す。
5. **`sandbox_status` は台帳の写しを `leases[]` で返す。** 貸出 1 件 = 1 行（`task` / `vmid` / `name` / `ip` / `pj` / `since` / `phase` / `url` / `vm_status`）で、チケット番号の順。`vm_status` は最後に成功した `ls` から `vmid` で引き、無ければ `null`。台帳が無い・読めないは `state_exists` / `state_error` で区別する。既存の `lent` はそのまま残す。

## 理由

- **なぜ annotations か。** サーバーを非同期にしても、呼び手が直列に送れば PM の観測は変わらない。並列に呼べることを宣言できる口はこれしかなく、書くのは事実（読むだけかどうか）なので嘘にならない。
- **なぜ同期の `ls` にしないか。** Proxmox への ssh は数秒かかる。`sandbox_status` は概況を見るたびに呼ぶツールなので、毎回数秒待たせるより「古い値 + 取り直し中」を返して次で最新にする方が運転が速い。
- **なぜ読み取りツールがジョブを起こしてよいか。** 増えるのは `console/jobs/` の記録だけで、Proxmox は読むだけ。10 分に 1 回・実行中なら起こさない・失敗直後も起こさない、の 3 条件で ssh の連打を防ぐ。`readOnlyHint: true` はこの意味で維持する（工場の状態＝チケット・run・VM の貸出は変えない）。
- **なぜ Web コンソールは自動化しないか。** 画面には「取得」ボタンと取得時刻が既にあり、人は古さを見て自分で押せる。自動化するのは、押す手が無い MCP の口だけにする。

## 結果（トレードオフ）

- `sandbox_status` を呼ぶと、10 分に 1 回まで `sandbox-ls` のジョブが増える。ジョブ一覧に人が押していない `sandbox ls` が並ぶ。
- ssh が落ちている間は 10 分ごとに 1 回失敗ジョブが増える（連打はしない）。理由は `ls_refresh_error` に出る。
- `readOnlyHint: true` のツールが記録を増やすのは、この 1 つだけの例外。増やすときは ADR を足す。
- `SANDBOX_STATE` を設定していない環境の動きは変わらない（既定は従来と同じパス）。
