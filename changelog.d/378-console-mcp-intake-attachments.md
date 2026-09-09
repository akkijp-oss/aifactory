### Added

- Web コンソールの起票画面とチケット画面からチケットに添付できる（ドラッグ＆ドロップと選択、複数可）。画像（png / jpg / gif / webp）はチケット画面にサムネイルで出て、その他はダウンロードのリンクになる。`×` で 1 件消せる（ADR-0041 の第 2 段）
- MCP に `ticket_attach`（`content_base64` か ctl 上の `path`）と `ticket_detach` を追加。`read_file` は添付の画像を image ブロックで返す（4 MiB まで）ので、AI セッションが添付を見られる
- `glue/bin/intake --attach FILE …`。起票したチケットに添付し、画像は LLM にも見せて本文の `## 現状` に読み取れた事実を書かせる

### Changed

- 添付のファイル名の制限を締めた（`lib/aifactory_attachments.py`）。上限 255 → **120 バイト**、Markdown の記法（`` ` `` `*` `[` `]` `<` `>` `|`）は `_` に置換、連続する空白は 1 つに。名前は依頼文にそのまま埋まるため。既に 121 バイト以上の名前で保存された添付は一覧・`kb detach` から見えなくなるので、`$AIFACTORY_WORKSPACE/kanban/attachments/<id>/` で手で改名すること
