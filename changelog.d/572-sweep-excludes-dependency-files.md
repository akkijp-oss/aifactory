### Fixed
- **runner: 工程の終わりの掃き寄せ（`sandbox: uncommitted changes by agent`）が依存ファイルを拾わない**。環境が解決し直しただけの `pnpm-lock.yaml` / `package.json` が意図した変更として PR に載り、CI のテストが 18 件赤になっていた（kumitate #527）。既定で `**/package.json` と `**/pnpm-lock.yaml` を対象から外し、作業ツリーごと HEAD の内容へ戻す。除外の一覧は `project.yml` の `sweep_exclude` で変えられる（`[]` で除外なし）。意図した依存の変更は実装役が自分で `git add` する（staged なファイルは除外されない）。時間上限・利用枠で切られたときの救済そのものは変えていない（ADR-0081）

### Added
- **runner: 掃き寄せが起きた事実とファイル一覧が記録に残る**。掃き寄せコミットの本文に拾ったファイルと除外して戻したファイルの一覧が入り（今までは本文ゼロ）、`state.json` の工程履歴の `swept` とコンソール / MCP の `outcome.swept`、runner の標準出力にも出る。差分を開かないと気づけない状態をやめた。`git add` 自体が失敗した工程は**戻しもコミットもせず**（書きかけを作業ツリーに残す）、その事実を `add_failed` として記録する
- **runner: run の開始時に作業ツリーの汚れを戻す**。checkout の直後と `prepare` の直後に追跡済みの変更が残っていれば HEAD の内容へ戻し、`state.json` の `dirty_at_start`（`outcome.dirty_at_start`）に残す。テンプレートや前の実行が残した変更が次の run の基準になるのを防ぐ。未追跡のファイルは触らず、run も止めない
