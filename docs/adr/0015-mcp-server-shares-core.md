# ADR-0015: 操作口は Web コンソール（HTTP）と MCP（stdio）の 2 つ。読み書きの正本は `console/lib/core.py` に 1 つ

日付: 2026-09-06 / 状態: 採用 / 決定者: メンテナ（「MCP で操作で読み書き操作できるようにしてほしい」）

## 状況
- Web コンソール（ADR-0013）で人がブラウザから読み書きできるようになった。次は AI セッション（Claude Code 等）が同じ読み書きを **ツールとして** 行いたい。今は AI が `kb` / `intake` / `dispatch` / `sandbox` を bash で叩き、ログを `cat` で読んでいる
- MCP（Model Context Protocol）なら、Claude Code / Claude Desktop / 他のクライアントから同じ操作が型付きのツールとして見える。stdio 転送は 1 行 1 メッセージの JSON-RPC 2.0 で、標準ライブラリで実装できる
- コンソールの `bin/console` に読み書きの判定（二重起動、入力検査、読める根の制限）が全部入っていた。MCP のために複製すると二重になる

## 決定
- `bin/console` から読み書きの本体を `console/lib/core.py` に切り出す。HTTP の `bin/console` と stdio の `bin/mcp` は **同じ関数を呼ぶ薄い口**。判定（`ApiError` / `Conflict`）は core にだけある
- `bin/mcp` は標準ライブラリだけの最小 MCP: `initialize` / `ping` / `tools/list` / `tools/call` / `resources/list` / `resources/read`。ツールは 20 本（overview / ticket_* / intake / dispatch / run_* / read_file / sandbox_* / job_* / logs / config）。resources はボード・台帳・チケット本文
- ジョブ記録 `console/jobs/` は両プロセスで共有する。二重起動の判定と meta の読み書きは `jobs/.lock` の flock で直列化する（プロセス内の threading.Lock だけでは足りない）
- 別プロセスが起動したジョブは、再起動後のプロセスが pid の消滅を見張って `ended`（終了コード不明）にする。kb run の結果は run の `state.json` と kb の状態が正
- 登録はリポジトリ直下の `.mcp.json`（timeout 10 分）。command は `bash -c 'exec "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}/console/bin/mcp"'`: 対話セッションでは Claude Code が `CLAUDE_PROJECT_DIR` を展開し、`-p` 等で未設定のときは git のトップレベルから引く（サブディレクトリから起動しても効く。実測で確認）。プロジェクトスコープなので、このリポジトリで Claude Code を開けば初回に承認を求められて使える
- MCP でも **状態を変えるのは既存 CLI 経由**（ADR-0013 と同じ）。長い操作はジョブで返し、`job_wait` で待てる（上限 570 秒、`.mcp.json` の timeout より短い）

## 理由
- 口を増やすたびに判定を複製すると、二重起動ガードや読める根の制限が食い違って穴になる。正本を 1 つにして口は薄く
- 標準ライブラリで書けるのは MCP の stdio が単純だから。SDK を入れると pip の版管理が要り、ADR-0013 の「依存ゼロで壊れない」が崩れる。必要になれば SDK に載せ替えられる程度の薄さにしておく
- flock を足すのは、コンソール（常駐）と MCP（Claude Code が起動）が同時に動くのが通常運転だから
- 孤児ジョブの見張りは、コードを更新して常駐を再起動するたびに「実行中のまま止まる」記録が残るのを避けるため

## 結果（トレードオフ）
- 良い: Claude Code から `mcp__aifactory__ticket_show` 等で工場を読み書きできる。人が Web、AI が MCP、どちらのジョブも同じ画面に出る
- 悪い: MCP の実装が自前なので、プロトコルの改版（現状 2024-11-05 / 2025-03-26 / 2025-06-18 のどれでも応答）に追従する責任がこちらにある
- 悪い: `resources` は読み物 3 種だけ。prompts は無い
- 実測（2026-09-06）: unittest 6 本（handshake / tools / resources / 書き込み / dry-run ジョブ待ち / 不正入力）。実際の Claude Code（Haiku、`--mcp-config .mcp.json`）から overview と ticket_show を呼んで正しい答えが返ることを確認
