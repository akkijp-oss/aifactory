### Fixed
- **制御系 sqlite の `database is locked` で run が落ちなくなった**。同時に走る run が多いとき、キュー DB のロックを
  待ち切れずに runner が落ち、worker が operation の取り消しでゲストごと止めるため、ゲスト内のコミット済み・未 push の
  実装が消えていた。ロックで弾かれたトランザクションは短くやり直し（`journal_mode=WAL` 併用）、runner の状態読みは
  一時的なロックで operation を取り消さなくなった。制御系の失敗で人へ返る run も、wip ブランチへ push してから終わる（ADR-0066）。
