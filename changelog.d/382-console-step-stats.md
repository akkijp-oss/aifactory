### Added
- **console に「統計」画面**（チケット 382）。agent の工程ごとの消費（ターン・所要・入力 / キャッシュ書込 / キャッシュ読出 / 出力・thinking の回数と本文が見える回数・ツール呼出・費用換算）を run の生イベント（`agent-*.jsonl`）から集め、期間と PJ で絞って、モデル別・工程別・日別・PJ 別と費用換算の高い工程の上位 20 を出す。API は `GET /api/stats?days=&pj=&dry=`、MCP は `stats`。読んだ結果は `stats-cache.json`（ジョブ記録の置き場）に置き、変わったファイルだけ読み直す
- runner の利用枠切れの判定に、実機で出た文言 "You've hit your session limit" を足した（rate_limit_event が無い経路の保険）
