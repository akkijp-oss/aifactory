### Fixed
- **添付: `.eml` や `.mht` をブラウザーから添付すると、エラーも出ないまま 0 件になっていた**。受け口
  （`console/bin/console` の `parse_multipart`）がブラウザーの付ける種類（`message/rfc822` /
  `multipart/related`）を「入れ子の multipart」と誤判定して捨てていた。ファイルかどうかを
  **ファイル名の有無**で決めるようにし、中身はバイトのまま受けるようにした。
