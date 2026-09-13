### Fixed
- **運用ガイド: `docs/macos-worker.md` に ADR-0066 の2つの記述を足した**。操作DBがWALで開くこと（`queue.sqlite3-wal` と `-shm` を一緒に扱う）と、制御系の失敗でrunが人へ返る回の保全（同じwip pushを試してからleaseとゲストを検査用に残し、記録済みの `wip_branch` は消さない）は、website版にしか無かった。運用者が先に読むのはリポジトリ直下の `docs/` なので、随伴ファイルを知らずにDBを移すと壊す余地があった。
