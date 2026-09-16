### Added
- **手元の端末にあるファイルを、そのままチケットに添付する CLI `console/bin/attach` を足しました**。
  `console/bin/attach 596 ~/Desktop/画面.png` のように使うと、既存の口（`POST /api/tickets/<id>/attach`）へ
  multipart で直接送ります。MCP（`ticket_attach`）は制御系 LXC の中で動くので `path` では手元 PC のファイルを
  読めず、`content_base64` は中身を引数に載せてしまう、という穴を埋めるものです。出力は成功なら応答の JSON 1 行
  （`id` / `added` / `attachments`）、失敗なら理由 1 行だけで、**ファイルの中身・その base64・合言葉はどこにも
  出しません**（ファイルの大きさに比例して長くなりません）。接続先は `AIFACTORY_CONSOLE_URL`（既定
  `http://127.0.0.1:8765`）、合言葉は `CONSOLE_TOKEN` で、どちらも環境変数か `~/.config/aifactory/mcp-remote.env`
  から読みます（合言葉は `ps` に出ないよう引数では受けません）。上限（1 ファイル 20 MiB / 合計 100 MiB）と
  名前の整えは今までどおりサーバー側の判定がそのまま働き、既存の 3 つの添付方法（multipart 直叩き /
  `content_base64` / `path`）は変わりません（ADR-0092）。
