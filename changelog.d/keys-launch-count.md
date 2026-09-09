### Changed
- **鍵プールの「使用回数」を、実際に `claude` を起動した回数にする**。`sandbox keys list` の `USES` は take / reinject で鍵を割り当てた回数で、Fable の工程が走らない run でも増えるため実使用と食い違った（2026-09-10 に `keiyukai_partner-n5` で誤解を招いた）。runner が agent を起動するたびに `sandbox keys used <名前>` で報告し、`keys list` / console の「鍵」画面 / MCP `keys_list` は `LAUNCHES`（起動回数）と最後に起動した日時を出す。割り当て回数は `ASSIGNED` として残す（鍵を選ぶ順番に使う）。
