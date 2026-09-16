### Added
- **票の参照（`related_issue` / `related_issue_access` / `related_ticket`）が run の依頼文と API に届くようになった**。`kb run` が 3 列を runner に渡し、依頼文の「## チケット」の直後に値が 1〜2 行出る（取得可否は「取りに行かない」「取りに行ってよい」の 1 語に言い換える）。参照を持たない票の依頼文は今までどおり（行も空行も増えない）。MCP / HTTP の `ticket_action(set, …)` と `ticket_new` からも 3 列を書けるようにした（`depends_on` と同じ扱い: キーが無ければ触らない・空文字列で消す）。運ぶのは値だけで、取りに行く / 行かないの規則は役割文書 1 枚のまま（#556 / ADR-0084 の申し送り）
