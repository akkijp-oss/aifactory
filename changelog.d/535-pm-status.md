### Added
- **管理役（PM）の状態を読む口**。`GET /api/pm[?pj=]` と MCP の `pm_status` が、`core.pm_status()` の導いた
  `idle` / `waiting` / `landing` / `blocked` を返します（読み取りだけで、何も起動しません。ADR-0074）。状態は保存せず、
  板・実行記録・ジョブから毎回導きます。**「取得できていない」と「0 件」は別の値**で返るので（`board.reason` /
  `runs.reason`、`next` は常に理由コードつきの object）、確かめられていない状態が「順調」に見えません。
