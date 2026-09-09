### Fixed
- **MCP: `job_wait` を待っている間に他のツールが応答しなくなる**。サーバーは待ちを別スレッドに逃がしていた（ADR-0028）が、`tools/list` が `annotations` を返していなかったため、クライアント（Claude Code）が「並列に呼べないツール」とみなして同じターンの呼び出しを直列に送っていた。全ツールに `annotations`（`title` / `readOnlyHint`、返却と停止には `destructiveHint`）を付けたので、`job_wait` 中でも `ticket_show` などがそのまま返る（ADR-0037）。
- **MCP: `sandbox_status` の `lent` が空に見える／貸出中 VM の IP が引けない**。貸出台帳の場所を環境変数 `SANDBOX_STATE`（`glue/bin/dispatch`・`workflow/bin/run` と同じ規則）で決めるようにし、テナント運用で CLI と別のファイルを読まないようにした。あわせて `state_exists` / `state_error` で「台帳が無い」と「貸出が無い」を区別し、`leases[]`（`task` / `vmid` / `name` / `ip` / `pj` / `since` / `phase` / `url` / `vm_status`）で貸出 1 件ずつ返す。`state.json` を ssh で直読みしなくてよくなった。

### Changed
- **MCP: `sandbox_status` が古い `sandbox ls` を裏で取り直す**。最後に成功した `ls` から 600 秒より古い（または一度も取れていない）とき、`sandbox ls` のジョブを起こして今回は古い値のまま `ls_refreshing: true` と `ls_refresh_job` を付けて返す。次の呼び出しで `pool_actual` / `free` が最新になる。実行中なら起こさず、直近の実行から 600 秒経つまでは起こさない。起こせないときは `ls_refresh_error` を添え、`sandbox_status` 自体は成功で返す。Web コンソールの「取得」は従来どおり手動（ADR-0037）。
