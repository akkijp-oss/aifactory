# aifactory 構想台帳（現在地・決定・未決・履歴）

起票: 2026-09-05 / 起票者: メンテナ（Claude Code との議論を経て）
2026-09-05 深夜に外部の個人メモからこのリポジトリへ移動。以後ここが正本
種別: メンテナ個人の開発基盤構想（受託 PJ ではない。タスク化はしない。着手を決めたら派生タスク候補から昇格）

## 着想ソース
- Dan Isler（IndieDevDan）の YouTube 動画 "FORGET Loop Engineering. Agentic Engineering is about THIS"（2026-09-05 視聴）
  - 出典の扱いは `source/README.md`。**動画・音声・文字起こしはリポジトリに含めない**（第三者の著作物）。要点だけ以下に抜く
  - **メンテナの発言ではない外部資料**
- 動画の主張（要約）
  - 「ループエンジニアリング」は SDLC の言い換え。正しくは **AI developer workflow（ADW）** を設計すること
  - 価値創出の3アクター = **エンジニア・エージェント・コード**。コードはトークン0円で最も信頼できる
  - エンジニアが出るのは **冒頭のプランニングと末尾のレビュー** の2点。間の lint / 型チェック / テスト / CI の合否分岐がエージェントへ戻るループ
  - 発展形: worktree 並列 → **エージェントごとのサンドボックス** → Kanban 起点のルーター → chore / bug / feature / hotfix ごとの特化ワークフロー = ソフトウェアファクトリー
  - 実践3箇条: ①最小から始める ②まず自分で end-to-end を通してから Mermaid 等で書き起こす ③スキルに詰め込まず **コードとエージェントを分離**（Agent SDK でビルド → lint → 同一セッションに差し戻し）

## このリポジトリでの位置づけ
- リポジトリ名 `aifactory` は 2026-09-05 にメンテナが「わかりやすく」で決定。起票時は private、2026-09-06 に公開リポジトリ（Apache-2.0）として整備
- **この台帳 = 今どうなっているか（現在地・未決・履歴）/ 各区画のディレクトリ = どう作るか・どう使うか**（`../sandbox/BUILD.md` 手順書・`STATUS.md` 進捗票・`OPERATIONS.md`・スクリプト）/ `adr/` = 判断の理由
- 別の AI が引き継ぐときは ルートの README「このリポジトリを読む AI へ」から入る
- 優先順位: **コストより品質**（メンテナの判断 2026-09-05）
- 1リポジトリ（モノレポ）で始める。区画間の契約が見つかる前に分割しない（ADR-0001）
- **運用データはリポジトリの外**（2026-09-06 公開化）。環境変数 `AIFACTORY_WORKSPACE`（既定 `<repo>/workspace/`、git 追跡外）の下に `projects/<pj>/`（PJ 定義）・`kanban/`（kanban.db / tickets / BOARD.md）・`runs/`（実行記録）・`logs/`（intake / dispatch）・`docs/`（私的メモ: 実機台帳・台帳の私有版・文字起こし）を置く。同梱サンプルの PJ 定義は `../examples/projects/kumitate/`（探索順は workspace → examples）
- 実機（Proxmox ホスト・ネットワーク）の台帳は private workspace の `docs/` に置き、リポジトリには含めない
- 仕組みの解説 HTML（図つき）: `aifactory-how-it-works.html`（2026-09-06）。sandbox の物理側は `sandbox-architecture.html`
- ドキュメントサイト（ja / en、37 ページずつ）: `../website/`（2026-09-06、MkDocs Material + static-i18n。デプロイ先は未決、GitHub Pages 用の手動ワークフローだけ用意）
- MCP サーバー（AI セッションから同じ読み書き）: `../console/bin/mcp` + `../.mcp.json`（2026-09-06、ADR-0015）
- Web コンソール（ボード / 実行記録の工程トラック / sandbox / 取り込み / ジョブ）: `../console/`（2026-09-06、Python 標準ライブラリ、127.0.0.1 専用。状態は既存 CLI 経由でしか変えない。ADR-0013）。**メンテナの Mac で launchd 常駐（`com.aifactory.console`、http://127.0.0.1:8765/）**。コードを変えたら `launchctl kickstart -k gui/$(id -u)/com.aifactory.console`

## 現在の方針（2026-09-05 確定分）

### 全体の分割（メンテナ案 + 議論で1つ追加）
| 区画 | 役割 | 現在地 |
|---|---|---|
| **Sandbox** | どこで動くか。隔離・並列・使い捨て。人が中に入って結果を見られる | **v0 構築完了（2026-09-06）**。Proxmox ホスト 1 台に PJ ごと 3 台 × 複数 PJ + 汎用 3 台のプール、`clean` スナップショット、Mac から tailnet 直結で 5 操作が動く。進捗と所見は `../sandbox/STATUS.md` |
| **Kanban** | 何をいつやるか。組織からの入口と状態管理。「ただのコード、エージェント不在」 | **v0 実装（2026-09-06）**。SQLite + `kanban/bin/kb`（ADR-0011）。採番・状態（todo / in_progress / review / blocked / done）・履歴・`kb run`（runner 呼び出しと結果反映）。既存チケット 11 件を取り込み済み。詳細は `../kanban/README.md` |
| **Workflow（AI engineering）** | どう進めるか。plan → build → test/lint ループ → review の節をコードの条件分岐でつなぐ | **定義（YAML + JSON Schema）と runner v1 が実機稼働（2026-09-06）**。workflow 6 本（hotfix / bug / feature / chore / research / merge-pr）、役割憲法 4 本。v0 で 2 周、v1 で research と merge-pr を無人完走。PR 6 本（ADR-0009、`../workflow/README.md`） |
| **つなぎ**（議論で追加） | ルーター・ステップ間の状態受け渡し・ログ。動画終盤の「各ステップの間の結果を置く場所」 | **v0 実装（2026-09-06、ADR-0012）**。`glue/bin/intake`（自由文 → チケット。pj / kind が決まらない分だけ LLM 1 回）と `glue/bin/dispatch`（todo を順に `kb run`。直列、project.yml とプール空きだけ見る）。ステップ間の状態は runner の `state.json` と artifact 回収に任せる |

- **Sandbox を最初に作る**（メンテナの判断 2026-09-05）。理由: チケットにもワークフロー内容にも依存せず汎用性が最も高い / 現行の worktree + cmux surface の次の一段 / インフラとして一番難しい
- ただし Sandbox 単体には価値がないので、**最小ワークフロー1本を並走させて要求を引き出す**（動画の「まず自分で end-to-end」に対応）
- Sandbox は **コードで叩ける契約**として定義する（エージェントに Proxmox を触らせない）
  - 入力: リポジトリと ref、チケット文脈（task-id）、必要な secrets
  - 操作: `take` / `ssh` / `url` / `reset` / `release`
  - 出力: diff または PR、テスト結果、スクショ等の成果物、ログ

### Sandbox v0 設計（2026-09-05 確定）
実行基盤はメンテナの Proxmox クラスタ（実機台帳は private workspace）。詳細設計は `../sandbox/README.md`。

| 項目 | 決定 | 根拠 |
|---|---|---|
| 実行形態 | **Proxmox VM**（LXC ではない） | Rails をネイティブで動かす。VM のほうが事故が少ない |
| ホスト | **大型ノード 1 台**（数十コア / 200GB 級 RAM。本文では `pve1`）。増設時は同クラスタの別ノード | 2026-09-05 に大型ノードが空ノードとして再参加。当初案の小型ノード（RAM 十数 GB）より余裕が桁違い |
| ディスク | ホストの `local-lvm`（LVM-thin）。linked clone とスナップショット可 | 共有ストレージ無し。ノード跨ぎのクローンは不可なので v0 は単一ノード |
| 粒度 | **使い回し + スナップショット巻き戻し**。PJ ごとのテンプレート VM → N 台のクローンを常駐プール → タスク終了時にクリーンなスナップショットへ rollback | 「シンプルに使い回したい」（メンテナ）と隔離の両立。ネイティブ実行なので DB も巻き戻しで初期化される |
| VM サイズ | 1台 4GB / 2 vCPU から。ホストなら 20台以上入るが v0 は **2〜3台** | メンテナ「2〜3台で様子見」。**実績（2026-09-06）**: Rails の rspec が重いため 1 台 8GB / 4 vCPU に変更、PJ ごと 3 台 + 汎用 3 台 |
| アプリ実行 | **VM 内ネイティブ**（Docker 不使用）。Ruby（mise）/ Node / PostgreSQL / Redis / ヘッドレス Chrome / gh / Claude Code をテンプレートに焼く | メンテナ決定 2026-09-05。Ruby / Node の版が PJ で違うので **テンプレートは PJ 単位** |
| ネットワーク | 専用 IP 空間 **10.77.0.0/16**（Proxmox SDN、ホスト上の simple zone、SNAT で外向き）。task-id から IP を派生（cloud-init で静的付与） | メンテナ「IP 空間を分けたい」 |
| Mac からの到達 | **(b) ゲートウェイ LXC 1台だけ tailnet に入れ、subnet router として 10.77.0.0/16 を広告**。VM 側に Tailscale は入れない | メンテナ選択 2026-09-05。VM に Tailscale を入れるとスナップショット巻き戻しでノード鍵が重複する |
| 名前解決 | ゲートウェイ LXC の dnsmasq が `task-{id}.sb.internal` を返す。Tailscale の split DNS で `sb.internal` をそこへ向ける | ブラウザは `http://task-{id}.sb.internal:3000`、作業確認は `ssh task-{id}` |
| エージェント配置 | **VM 内で Claude Code を動かす**（1エージェント1サンドボックス）。cmux の surface は ssh セッションになる | Mac から遠隔操作する案は編集のたびに往復して遅い。cmux-devteam の「実装担当ごとに別 worktree」を「別 VM」に置き換えるだけ |
| 認証 | **`claude setup-token` の長期トークン**を `CLAUDE_CODE_OAUTH_TOKEN` として `take` 時に注入。トークンは Mac 側1箇所に置き、VM には残さず巻き戻しで消える | メンテナ「setup-token または OAuth」。通常 OAuth の焼き込みはリフレッシュトークンの取り合いが起きうるので不採用 |
| CI/CD 節 | 新設せず既存の self-hosted runner に繋ぐ | 既存資産 |
| 制御系の置き場 | Kanban とルーターは軽いので **軽量ノード**側 | 「sandbox = 大型ノード、kanban とつなぎ = 軽量ノード、workflow = 各 VM 内」で3分割が物理配置と一致 |

#### v0 の構築手順（**2026-09-06 に全 6 ステップ実施済み**。正本は `../sandbox/BUILD.md`、実績は `../sandbox/STATUS.md`。ここは要約）
1. Proxmox ホストに SDN simple zone `sb` + vnet `sbnet`（10.77.0.0/16、gw 10.77.0.1、SNAT on）
2. ゲートウェイ LXC `sb-gw`（Debian、eth0=vmbr0 / eth1=sbnet）: Tailscale subnet router（10.77.0.0/16 広告、**メンテナが管理コンソールで承認**）+ dnsmasq（`*.sb.internal`）
3. ベーステンプレート VM（Ubuntu 24.04 cloud image + cloud-init + mise/Node/PostgreSQL/Redis/Chrome/gh/Claude Code）→ `qm template`
4. PJ テンプレート（**2026-09-06 に複数 PJ を投入**。公開サンプルは `kumitate`）: ベースから clone → リポジトリ clone・依存・seed 投入 → スナップショット `clean`。GitHub の push / PR 権限は GitHub App `aifactory-sandbox` の 1 時間トークン（ADR-0008）
5. `sandbox` CLI（Mac 側 bash、中身は ssh 越しの `qm clone / set --ipconfig0 / snapshot / rollback`）: `take <pj> <task-id>` / `ssh` / `url` / `reset` / `release`
6. 動作確認: cmux の surface から `ssh task-001` で Claude Code を起動し、lint → テストまで1周（実績: v0 spin で 2 周、v1 runner で research + merge-pr。所見は `../sandbox/STATUS.md`「1周の所見」）

### 前提リスク
- ホストの電源投入は **物理ボタン**（リモート管理未設定）。落ちると遠隔で戻せない
- 同日に別セッションがクラスタ整備（再起動含む）を行うことがある。**構築着手前にそちらの作業完了を確認する**
- テンプレートに焼く gh 認証・SSH 鍵は、巻き戻しでも残る（テンプレート由来）。漏洩面はプール VM の隔離で担保する前提

## やりたいこと（未確定アイデア）
- ~~【構想/方向性】Kanban の実体を個人タスク台帳（SQLite）にするか termboard にするか~~ → 2026-09-06 メンテナ決定: リポジトリ内に自前 SQLite + CLI（ADR-0011）。外部の個人タスク台帳は ID（#101〜）が既存 task-id と衝突し粒度も個人タスクなので流用しない。termboard / Notion は取り込み口として後で足す
- ~~【構想/方向性】Workflow v0 = 「build agent → lint/typecheck（コード）→ 失敗なら同一セッションへ差し戻し」の1ループを Agent SDK で書く（動画の助言③）。スキル1本に詰めない~~ → 2026-09-06 に YAML 定義 + Python runner v1 で実現（Agent SDK ではなく `claude -p` を VM 内で呼ぶ形。ADR-0009）
- 【構想/方向性】ゲートの並列化・差分実行。1 周の時間の大半がゲート（Rails PJ の rspec 57 分、kumitate test 15 分）で agent は数分。**調査済み（チケット 206、2026-09-06）**: kumitate のゲートは逐次で約 16.5 分、うち apps/web の test / test:dom / typecheck が 8 割超。差分実行はパッケージ単位なら可能だが効果は「web を触らない PR の割合」次第なので**保留**。先に (a) 直近 PR の web 非接触率を `git log --name-only` で調べる (b) `packages/db/.data/` を kumitate の `.gitignore` に足す（turbo の変更検出を汚染）。詳細は `$AIFACTORY_WORKSPACE/runs/2026-09-06-kumitate-206/work/summary.md`（git 追跡外）
- 【構想/方向性】画面確認（スクショを Mac に回収）を workflow に組み込む（`url` は Web コンソールの sandbox 画面に出るようになった 2026-09-06）
- 【論点/保留】hotfix 用の「複数 sandbox で競争させて最速を採る」パターン。プール台数が増えてから
- 【論点/保留】ノード跨ぎのプール。共有ストレージ無しなので、テンプレートを両ノードに複製するか VXLAN zone にするか

## 派生タスク候補
- ~~Sandbox v0 構築（上記手順 1〜6）~~ 済（2026-09-06）
- ~~`claude setup-token` の実行と保管場所の決定~~ 済。PJ 別に `~/.config/sandbox/pj/<pj>.env`（ADR-0006）。全 PJ 保存済み
- ~~最初の PJ テンプレートの選定~~ 済
- ~~Kanban の実体の選定と v0 実装~~ 済（2026-09-06、ADR-0011）
- ~~glue のルーター~~ 済（2026-09-06、intake / dispatch。ADR-0012）
- 外部の入口（termboard / Notion / 個人タスク台帳）→ `intake` に流す薄い層
- kumitate: `packages/db/.data/` を `.gitignore` に追加（206 の申し送り。chore チケット 1 枚で済む）
- kumitate: 直近 N 件の PR の apps/web 非接触率を調べ、差分実行の去就を決める（206 の次の一手）
- ~~🧑 GitHub App に Actions: Read を足す~~ 済（2026-09-06 12:30、メンテナ）。任意で Checks: Read（`gh pr checks` 用）
- `project.yml` の無い PJ の `project.yml`（無いと dispatch が blocked にする）
- ゲートの差分実行 / dispatch の PJ 単位並列

## 方針の履歴（新しい順）
- **2026-09-06（公開化）**: 公開リポジトリ（Apache-2.0）として整備。運用データ（PJ 定義・kanban.db・tickets・runs・logs）を `AIFACTORY_WORKSPACE`（既定 `workspace/`、git 追跡外）へ出し、`sandbox/templates/` は枠組み（base / env.example / ssh_config.example / launchd）だけに。`examples/projects/kumitate/` を同梱サンプルに。実機台帳・文字起こし・この台帳の私有版は private workspace へ。ADR・解説 HTML・各 README の人名と私設インフラ名を一般化（判断は変えない）
- **2026-09-06（夜・3）**: MCP サーバー（ADR-0015）。メンテナ「MCP で操作で読み書き操作できるようにしてほしい」。読み書きの本体を `console/lib/core.py` に切り出し、HTTP（console）と stdio（mcp）の 2 口に。ツール 20 本 + resources 3 種、`.mcp.json` で登録。unittest 6 本、実 Claude Code（Haiku）から呼べることを確認
- **2026-09-06（夜・2）**: コンソールを実運用に。unittest（console 14 本 / runner 7 本）、`console/bin/install.sh --launchd` で常駐登録（メンテナの Mac、port 8765）。コンソールだけで intake → チケット 206（kumitate research）→ 実行 → 実機 VM（sb-kumitate-02）で research 18 分 + judge 1 分 → 完了・返却まで通した（合計 19 分、$2.63）。逐次ログは 25 KB、生 JSONL は 660 KB（git 外にした判断が妥当だった）
- **2026-09-06（夜）**: runner の step 出力を逐次書き込みに（ADR-0014）。agent step は `claude -p --output-format stream-json` を 1 行ずつ人が読める形に起こして `agent-*.log` へ、生は `.jsonl`（git 追跡外）。`state.json` に `current`。コンソールが実行中 step のログを自動で追う。website に「Web コンソール」ページ（ja / en）を追加し、関連 9 ページを更新
- **2026-09-06（夕）**: Web コンソール v0（`console/`、ADR-0013）。メンテナ「Web コンソールが欲しい」。標準ライブラリの HTTP サーバー + 素の JS で、ボード・チケット操作（kb run / 状態遷移 / set / sync）・run の工程トラックとログ・sandbox 貸出と release・intake / kb new / dispatch・ジョブの追い読みと停止。複製 DB で検証、実機 `sandbox ls` も通した
- **2026-09-06（昼）**: glue v0（intake / dispatch、ADR-0012）。自由文から Rails + MySQL の PJ の seeds 不具合を起票（204）、`project.yml` の無い PJ の e2e 不具合（205）は blocked。sandbox CLI が actions:read を要求し無ければ従来権限に落ちるように（App 側の権限追加はメンテナ待ち）。同時セッションの約束をルート README に明記
- **2026-09-06（午前）**: 全 PJ のトークン保存で認証が揃う（VM 内で `claude -p` 応答を確認）。台帳を実機に合わせて更新。Kanban v0 を実装（メンテナ決定: リポジトリ内 SQLite + CLI、ADR-0011）。既存チケット 11 件を取り込み、`workflow/tickets/` を kanban の `tickets/` へ移動。別 PJ の PR 1 本がマージ
- **2026-09-06（深夜〜朝）**: Sandbox v0 を Proxmox ホストに実機構築（Step 0〜6 完了）。複数 PJ のテンプレート + プールを作成。GitHub App で push 権限（ADR-0008）。workflow 区画を定義（YAML + Schema、ADR-0009）と runner v1 で実装し、kumitate ほか 1 PJ で 2 周 + research + Rails PJ の merge-pr 2 本を無人完走（PR 6 本。kumitate は #293 / #294）。所見: 時間の大半はゲート、`known_red_gates` 導入、自動コミットは `git add -u` 限定
- **2026-09-05（深夜）**: この台帳を外部の個人メモから aifactory `docs/` へ移動して整理（メンテナの判断）。動画・音声・文字起こしはリポジトリ外に置く
- **2026-09-05（夜）**: 実装リポジトリ `akkijp/aifactory` を作成。Sandbox 区画の README / BUILD / STATUS / OPERATIONS / スクリプト / CLI / ADR 5本を書き切る（すべて未実行）。名前はメンテナが「わかりやすく aifactory」と決定。品質優先の指示
- **2026-09-05**: 着想。動画を文字起こし → メンテナが「sandbox / kanban / AI engineering の3分割、sandbox が最も汎用」と提起 → 議論で「つなぎ」を追加。Sandbox v0 を Proxmox VM で設計（ネイティブ実行、使い回し+巻き戻し、(b) ゲートウェイ LXC の subnet router、setup-token 注入）。同日にクラスタへ大型ノードが空ノードとして再参加したため、v0 ホストを当初案の小型ノードから大型ノードへ変更
