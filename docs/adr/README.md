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
| 0017 | 制御系（console / docs / runner / workspace）も Proxmox 上の LXC に置き、テナント（貸出先の組織）ごとに網・VMID 帯・firewall・リソースプール・API トークン・制御系を分ける。`sandbox` CLI は API モードでプール限定の権限だけを持つ | 採用（0013 を拡張、0004 の到達経路をテナントの tailnet に一般化） |
| 0018 | MacはGo製の単一バイナリのpull workerとして接続する。workflowは制御系に集約し、個別認証・永続操作キューで受け渡す | 採用（初期通信基盤を実装） |
| 0019 | コンソールの操作は危険性に比例した摩擦で守る（可逆は確認なし + 元に戻す、不可逆は番号入力）。文言は `console/static/strings.js` に集め、`console/UX.md` の約束を unittest で検査する | 採用（0013 を拡張） |
| 0020 | 共通workflowからmacOS pull backendを使い、run単位のlease、専用ゲストのclone・削除、秘密stdinと成果物回収を扱う | 採用（0018を拡張） |
| 0021 | コンソールは内側の網のアドレス（グローバルでない IP）には合言葉なしで bind できる。0.0.0.0 とグローバルは合言葉必須のまま | 採用（0017 の 5 を緩める） |
| 0022 | 専用Windows VMのサービスで共通pull queueを使い、一般ユーザー実行・Job Object・workspace返却を行う | 採用 |

- [0023: 専用VMのcomputer-use](0023-computer-use.md)

- [0024: 単体Linuxワーカー](0024-standalone-linux-worker.md)

- [0025: 実行記録の停止理由はコンソール側で導く](0025-run-outcome-derived-in-console.md)

- [0026: 起票・配車のログの表はコンソール側で導く](0026-logs-table-derived-in-console.md)
