### Changed
- **`dispatch` も先行条件（`depends_on`）を見る**。未完了の先行票を持つ票は回さずに飛ばし、理由（どの票を、どの先行票が
  未完了だから）を標準出力と `logs/dispatch.log` に残す。これまでは票を選ぶ経路が 2 つあり、`pm_status` は先行条件を見るのに
  `dispatch` からは素通りできた（止めてある票を機械が起動できた）。判定は console と同じ `core` の 1 か所を通る。
  `depends_on` を持たない票の挙動は変わらない（ADR-0078）。
