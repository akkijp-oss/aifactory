### Added
- **sandbox: 使われていないプール VM を自動で止めて CPU / メモリを空ける**。貸し出されておらず、最後に使われてから 3 時間（`SB_IDLE_STOP_HOURS`、`0` で無効、`pj/<pj>.env` で PJ 別に上書き可）経ったプール VM を、制御系の `aifactory-idle-stop.timer` が 15 分ごとに停止する（`sandbox idle-stop [--hours N] [--dry-run]`）。止まった VM は次の貸出（`take`）が自動で起動し、そのぶんの待ちはログに出る。制御系の `ctl` / `gw` は対象にならない。`sandbox ls` とコンソール・MCP は「節電で停止中」と説明する（ADR-0033）。
