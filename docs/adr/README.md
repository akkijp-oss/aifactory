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

- [0026: 記録する時刻はオフセット付き ISO 8601 にする](0026-timestamps-carry-utc-offset.md)
- [0027: 起票・配車のログの表はコンソール側で導く](0027-logs-table-derived-in-console.md)
- [0028: MCP の job_wait は読み取りスレッドから外して待つ](0028-mcp-job-wait-runs-off-the-reader-thread.md)
- [0029: 長期トークンの差し替えは `sandbox token rotate` 1 コマンドで、global・全 PJ・`ctl.env` を同時に更新する](0029-token-rotate-across-pj-and-ctl-env.md)
- [0030: 制御系の gh トークンは GitHub App から都度払い出す／kind は種別、workflow は実行方法](0030-control-plane-gh-token-from-app.md)
- [0031: VM の空き待ちは runner が 1 か所で行う／上限超過は未着手に戻す](0031-wait-for-a-free-vm-in-the-runner.md)
- [0032: PR を作る直前に base を取り込む／`CHANGELOG` は `changelog.d` に分ける](0032-sync-base-before-pr-and-changelog-d.md)
- [0033: 使われていないプール VM は止める。起こすのは `take` の役目](0033-stop-idle-pool-vms.md)
- [0034: ワーカーのログ上限は切り捨てで扱う。画像の base64 は記録前に置き換える](0034-worker-log-limit-truncates-and-strips-images.md)
- [0035: idle-stop は 24 時間で候補にし、候補が 10 台を超えた分だけ古い順に止める](0035-idle-stop-candidates-and-cutoff.md)
- [0036: 止まった run の再開は「新しい VM で、wip ブランチの続きから、指定の step」で行う](0036-resume-from-step-on-a-new-vm.md)
- [0037: MCP のツールに annotations を付ける／`sandbox_status` は古ければ裏で `sandbox ls` を起こす](0037-mcp-tool-annotations-and-sandbox-status-refresh.md)
- [0038: 貸出直後の準備は `project.yml` の `prepare` で行い、「base でも赤いゲート」は runner が base で回して確かめる](0038-prepare-hook-and-base-red-gate-check.md)
- [0039: 人間の後始末は kb が run 記録に転記する（`result` は上書きせず `human` を別に持つ）](0039-human-closeout-recorded-by-kb.md)
- [0040: 赤いゲートのログは `work/gates/<名前>.log` に残し、実装役へ戻す依頼文の抜粋はそこから作る](0040-gate-logs-kept-in-work-gates.md)
- [0041: チケットの添付は workspace の `kanban/attachments/<id>/` に置き、本文には書かない](0041-ticket-attachments-in-workspace.md)
