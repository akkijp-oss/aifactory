### Added
- **鍵の利用枠切れ（トークン切れ）で止まった run を、解除後に機械が続きから回す**（チケット 380 / ADR-0043）。agent の `claude -p` が利用枠（5 時間 / 7 日の窓、429）で拒否されると、runner が追跡済みの変更を `wip: usage limit` としてコミットして退避ブランチに保全し、戻しの回数を消費せず止まる。`state.json` に `failure: "quota"` / `quota_type` / `retry_after`（解除見込み） / `quota_hits`、`resume_step` はその工程自身。kb はチケットを **todo** に戻し、制御系の systemd timer `aifactory-resume.timer`（5 分ごと。`sandbox/bin/install.sh --systemd` で登録）が `dispatch --resume-paused` を呼び、解除時刻を過ぎたものを `kb run <id> --from` で続きから回す。鍵そのものが無効・失効・残高不足なら `failure: "key"` で blocked（鍵を直してから `kb run --from`）
- `kb resumable [--pj P] [--json]`: 一時停止中のチケットと、いつから続きを回せるか。`dispatch` も一時停止中のチケットは解除前なら飛ばし、解除後は初めからではなく `--from` で続きから回す
- console / MCP の run の結果に「利用枠の上限で中断・HH:MM 以降に自動再開」「鍵が使えず中断」が出る

### Changed
- runner の逐次ログに `rate limit: rejected (five_hour; resets …)` / `rate limit: warning …` の行が出る（claude CLI の `rate_limit_event`）
