### Added
- **チケットに画像やファイルを添付できるようにした**。`kb attach <id> <file>...` / `kb attachments <id>` / `kb detach <id> <name>` と `kb new --attach FILE`。添付は `$AIFACTORY_WORKSPACE/kanban/attachments/<id>/` に置かれ（本文には書かない）、`kb show`・コンソールのチケット画面・MCP `ticket_show` に一覧が出る。1 ファイル 20 MiB・1 チケット合計 100 MiB まで（ADR-0041）
- **run を起動すると添付が VM に届く**。runner が `~/work/<id>/attachments/` に置き、各 step の依頼文に `- 添付: …（画像・PDF は Read で見ること）` を足すので、agent がスクリーンショットや仕様書 PDF を直接見て作業できる。添付が無いチケットの依頼文は変わらない。今のところ Proxmox backend のみ（pull backend は別途）
