### Fixed
- **MCP: スキーマに無い引数・必須の抜けをツールエラーで返す**。`tools/list` では `additionalProperties: false` と
  `required` を配っていたのに `tools/call` が検査しておらず、打ち間違えたキー（`depend_on` など）は
  **「成功」を返したまま黙って捨てられていた**。呼び手（MCP 越しの AI・スクリプト）が書けたと誤読する。
  これからは受け付けるキーの一覧を添えて `isError` で返る。値の型と意味の判定は今までどおり（ADR-0082）。
