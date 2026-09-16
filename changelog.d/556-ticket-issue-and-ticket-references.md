### Added
- **票の参照を「外部 issue」と「内部の票番号」に分けて持てるようになった**。`kb new` / `kb set` の `--issue URL`（`related_issue`。カンマ区切りで複数可・`''` で消す）、`--issue-access readable|unreadable`（`related_issue_access`。**書かなければ `unreadable`**）、`--ticket IDS`（`related_ticket`。aifactory の内部票番号）で書け、`kb show` / `kb history` と MCP / HTTP API の `ticket_show` / `ticket_list` から読める。内部番号を `--issue` に書くと断って `--ticket` を案内し、URL を `--ticket` に書くこともできない——**参照が「取りに行けるもの」かどうかを、本文の文字列でなく構造で分ける**のが目的。本文の自由文は解釈せず、既存票の移行もしない（新しい起票から構造化する）。これらを書いていない票の挙動は変わらない（列が無かった頃の `kanban.db` には `kb` が起動時に足す）。背景: 本文に自由文で書かれた `https://github.com/<o>/<r>/issues/393` が実は内部の票番号で、しかも sandbox のトークンは issues に 403（`Resource not accessible by integration`）を返すため、researcher が **4 回連続で取得に失敗**して約 20 秒とトークンを焼いた（ADR-0084）

### Changed
- **researcher の役割文書に「sandbox のトークンでは GitHub の issues と feedback-assets を読めない」を明記した**。票に issue の URL が書いてあっても取りに行かず、票の本文と実コードだけで進める（`related_issue` に `readable` と付いているものだけが例外）。これまで管理役が票ごとに手で書き足していた読み替えを、役割文書 1 枚の 1 行に寄せた（ADR-0084）
