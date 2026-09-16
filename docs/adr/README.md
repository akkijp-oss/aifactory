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
- [0042: PR は `base_branch`（aifactory は `develop`）宛てに作り、条件を満たせば runner がマージする。main への昇格は人間](0042-auto-merge-into-develop.md)
- [0043: 鍵の利用枠切れで止まった run は「wip を保全して一時停止し、解除後に機械が続きから回す」](0043-pause-and-resume-on-usage-limit.md)
- [0044: Claude の鍵は PJ ではなく制御系のプールで持ち、take がフラグで選ぶ](0044-claude-key-pool-in-control-plane.md)
- [0045: VM に渡す Claude の鍵は鍵プールを正本にし、`sandbox token set <pj> claude` は非推奨にする](0045-key-pool-is-the-source-token-set-deprecated.md)
- [0046: 要る用途の Claude の鍵が鍵プールに無ければ、env の鍵に落ちず一時停止し、鍵が登録されたら機械が回し直す](0046-pause-when-no-key.md)
- [0047: `--resume` の開始工程は `next` ではなく工程履歴から決め、続きが無い run は VM に触る前に止める](0047-resume-start-step-from-history.md)
- [0048: チケットの `note` は先頭の `[run] ` 行だけが機械のもので、残りの行は人のもの](0048-run-note-line-keeps-human-note.md)
- [0049: pull worker を他の run が使っているのは「待てば解ける失敗」（`PoolBusy`）として扱う](0049-pull-worker-lease-wait-is-pool-busy.md)
- [0050: 「PR がどうなったか」は GitHub を正本に kb sync が見る（gh が使えなければ黙って飛ばす）](0050-pr-state-from-github-in-kb-sync.md)
- [0051: run の工程遷移は run_wait で待つ／経過秒とゲート一覧は表示側で導く](0051-mcp-run-wait-and-derived-run-progress.md)
- [0052: PJ 定義は MCP から読み書きする。書き先は workspace 側だけで、`examples/projects/` は読むだけ](0052-mcp-project-definition-read-write.md)
- [0053: 軽微な FAIL は `severity: minor` で 1 周だけ延長する。再開に渡す review.md は FAIL のときだけ、二重再開は断る](0053-review-severity-and-resume-guard.md)
- [0054: ボードのカードは div + 題名リンク + 工程リンクの兄弟構成にし、突き合わせ用の稼働中 run（`runs_live`）には上限を掛けない](0054-board-card-live-step.md)
- [0055: 統計の日別は工程の時刻を「見る側の時間帯」に直した日付で切る（run 名の日付は変えない）](0055-stats-by-day-in-the-viewers-timezone.md)
- [0056: チケット詳細は「読む → 状況 → 操作 → 記録」の順に DOM を並べ、操作領域の強いボタンは 1 つにする](0056-ticket-detail-reading-order.md)
- [0057: ゲストの表示解像度は PJ 定義 > worker 設定 > 基準 VM のまま、の順で決める（scale は未対応）](0057-guest-display-resolution.md)
- [0058: チケット詳細の項目編集は、チケット単位の下書き（sessionStorage）で守る](0058-ticket-edit-draft-per-ticket.md)
- [0059: pull backend の code step は kit の script を guest の中で走らせる（`SB_LOCAL`）。対応表はテストで固定する](0059-pull-backend-code-steps-run-in-the-guest.md)
- [0060: VM に渡す Claude の鍵の出どころは鍵プールだけにし、`sandbox` の env ファイル経路（`token set claude` と空プール時のフォールバック）を廃止する](0060-claude-keys-come-only-from-the-pool.md)
- [0061: チケットの `note` は複数行を正本とし、画面の編集欄は textarea・保存は無変換にする](0061-ticket-note-is-multiline-and-saved-verbatim.md)
- [0062: 実効モデルと分岐の解決規則は `lib/aifactory_workflow.py` の 1 か所に置き、設定画面は静的な定義だけから解く](0062-workflow-resolution-rules-in-lib.md)
- [0063: 起票は「方式を選ぶ → 入力する → 確かめて登録する」の 1 本道にし、1 画面 1 方式にする](0063-intake-is-one-mode-at-a-time.md)
- [0064: チケットの PR は console 側で「チケットの `pr` 列 + 実行記録の `pr_url`」から解決し、未登録を「存在しない」と言わない](0064-ticket-pr-resolved-in-console.md)
- [0065: モデルの設定は console が作業ツリーの `workflow/kit/` を直接書き、工程単位の `model` を新設する](0065-console-edits-model-settings-in-the-work-tree.md)
- [0066: 制御系 sqlite のロックは「長く待つ」のではなく「トランザクションをやり直す」（WAL 併用。runner の poll は op を取り消さない）](0066-control-sqlite-retries-the-transaction.md)
- [0067: worker はゲストを止める前に作業ブランチを wip へ push する（保全コマンドは制御系が payload で渡す）](0067-worker-preserves-work-before-stopping-the-guest.md)
- [0068: 止まったゲストは `guest-start` で起動し直し、`--resume` がそれを自動で呼ぶ（起動できない回は作り直さず止まる）](0068-guest-start-restarts-a-stopped-guest.md)
- [0069: ゲストの時計は貸出のたびに合わせ、それでもずれていたら run を止める（記録の日付は「合っている」を前提にしない）](0069-guest-clock-is-synced-at-lease-and-skew-fails-the-run.md)
- [0070: 制御系は `main` 追従のままにし、「develop に着地したが未配備」と「配った PJ 定義と正本のズレ」を機械で見せる](0070-control-plane-tracks-main-and-shows-what-is-not-deployed.md)
- [0071: sandbox の Go は PJ テンプレートに焼き、版は `workers/go.mod` から読む。満たせない run は prepare で止める](0071-go-toolchain-is-baked-from-go-mod.md)
- [0072: モデルは「Agent + モデル名（表示名）」の 2 段で選ぶ。表示名と ID の固定の表を console に 1 つ持つ](0072-console-picks-a-model-by-display-name.md)
- [0073: console から直せるのは「その工程の yml ブロック 1 つ」まで。id と担い手の種類は固定する](0073-console-edits-one-step-block-of-a-workflow.md)
- [0074: AI Factory Manager（PM）は console の中の `pm_tick()` として動かす。tick は 1 段だけ進める oneshot で、マージの判断は持たない](0074-pm-loop-runs-inside-console-as-a-oneshot-tick.md)
- [0075: base 確認は自分のログ（`~/gates/<名前>.base.log`）に書く。run が残す証跡は後続の処理で壊さない](0075-base-check-writes-its-own-gate-log.md)
- [0076: ゲストの PATH はゲストが答える。ログイン環境を起点に版管理ツール（mise）へ訊き、制御系は道具を列挙しない](0076-guest-path-is-answered-by-the-guest.md)
- [0077: 票の先行条件は人が書く 1 列（`depends_on`）にする。PM はそれだけを読み、本文の自由文は解釈しない](0077-ticket-prerequisites-are-a-column-the-pm-reads.md)
- [0078: 配車（dispatch）も先行条件を見る。判定は core の 1 か所を通し、飛ばした理由を残す](0078-dispatch-reads-prerequisites-through-core.md)
- [0079: 配車の下見は「実際に回る票」を見せる。選び直しは core の 1 か所（`pm_pick_next`）を通す](0079-preview-picks-the-same-ticket-as-dispatch.md)
- [0080: 実行環境の「できないこと」は PJ 定義が宣言し、完了条件との照合は警告だけにする。判定は 1 か所、未宣言は照合しない](0080-environment-capabilities-are-declared-and-checked-as-a-warning.md)
- [0081: 掃き寄せは救済であって成果物の判断ではない。依存ファイルは拾わず、拾ったものは必ず記録に残す](0081-the-sweep-is-a-rescue-and-does-not-carry-dependency-files.md)
- [0082: MCP は配ったスキーマを入口で強制する。未知のキーと必須の抜けはツールエラーにし、値の型と意味の判定は core に残す](0082-mcp-enforces-its-declared-argument-schema.md)
- [0083: 私有 PJ 名は 1 つ残らず検査語に載せ、書き換えられない既存 ADR だけをファイル名で名指しして例外にする](0083-private-project-names-are-all-in-the-pattern-and-old-adrs-are-named-exceptions.md)
- [0084: 票の参照は「外部 issue（取得可否つき）」と「内部の票番号」の 2 列に分ける。読めない参照は役割文書が名指しで断る](0084-ticket-references-are-split-into-external-issue-and-internal-number.md)
- [0085: VM の成果物は「列挙 → 上限で選別 → 1 接続で取得 → 届いたか検証 → 記録」で回収する。落としたものは必ず理由が残る](0085-artifacts-are-listed-selected-and-verified.md)
- [0086: 人が動くまで解けない一時停止だけが残った板でも PM の `state` は `idle` のまま。人の出番は状態ではなく facts と画面で伝える](0086-human-unblockable-pauses-stay-idle-in-the-pm.md)
- [0087: 鍵プールの残量（利用枠）は制御系が定期的に観測し、「鍵」画面と `keys_list` に窓ごとの残り %・回復時刻・ペースを出す](0087-key-pool-quota-probe.md)
- [0088: 証明可能に死んでいる run でも `kb run-note` の門は開けない。記録上の後始末は退避で扱い、`finished` の有無という見方を 1 つに保つ](0088-provably-dead-runs-are-not-closed-through-the-run-note-gate.md)
- [0089: console の左ナビは「頻度」ではなく「何をする画面か」で 3 群に分ける。見出しはリンクにせず、選択は色と `aria-current` の両方で示す](0089-console-nav-is-grouped-by-job-not-by-frequency.md)
