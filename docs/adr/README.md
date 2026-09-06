# ADR（Architecture Decision Records）

設計判断を1判断1ファイルで残す。**既存の ADR は書き換えない**。判断を変えるときは新しい番号で「supersedes 000X」と書く。

形式: `NNNN-slug.md`。節は「状況 / 決定 / 理由 / 結果（トレードオフ）/ 状態」。

2026-09-06 の公開化にあたり、既存 ADR の人名・私設インフラ名・私有 PJ 名・自宅 LAN の事実を一般化し、運用データのパスを `$AIFACTORY_WORKSPACE`（既定 `<repo>/workspace/`）に合わせた。判断と理由は変えていない。

| 番号 | 判断 | 状態 |
|---|---|---|
| 0001 | 1リポジトリ（モノレポ）で始める | 採用 |
| 0002 | sandbox は Proxmox VM、アプリは VM 内ネイティブ | 採用 |
| 0003 | プールは使い回し + スナップショット巻き戻し | 採用 |
| 0004 | Mac からの到達はゲートウェイ LXC の Tailscale subnet router | 採用 |
| 0005 | Claude Code 認証は setup-token の長期トークンを take 時に tmpfs 注入 | 採用 |
| 0006 | トークンは PJ ごとに持ち、貸出中の VM にも差し替えを反映（`token` / `reinject`） | 採用（0005 を拡張） |
| 0007 | 複数 PJ 同居: テンプレート 911x を PJ ごとに、VM の IP は VMID から導く | 採用 |
| 0008 | GitHub の push / PR 権限は GitHub App の installation token を take のたびに払い出す | 採用 |
| 0009 | workflow の定義は YAML + JSON Schema、手順は常備側に 1 つ、PJ 固有は事実と方針だけ | 採用 |
| 0010 | sandbox VM の通信はインターネットと sb-gw の DNS だけ。LAN・他 VM・tailnet へは出さない | 採用 |
| 0011 | kanban はリポジトリ内の SQLite + CLI（`kb`）。外部システム（個人タスク台帳 / termboard / Notion）は取り込み口として後から足す | 採用 |
| 0012 | glue は取り込み（LLM 1 回）と配車（ただのコード）の 2 本。ステップ間の状態は runner に任せる | 採用 |
| 0013 | Web コンソールは Mac ローカルの Python 標準ライブラリ製。状態は既存 CLI 経由でしか変えない | 採用 |
| 0014 | agent step の出力は stream-json を人が読める形に起こして逐次書き、生イベントはローカルにだけ残す | 採用 |
| 0015 | 操作口は Web コンソール（HTTP）と MCP（stdio）の 2 つ。読み書きの正本は `console/lib/core.py` に 1 つ | 採用 |
| 0016 | 枠組み（リポジトリ）と運用データ（`AIFACTORY_WORKSPACE`）を分ける。置き場の判断は `lib/aifactory_paths.py` に 1 つ | 採用 |
