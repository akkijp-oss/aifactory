# sandbox 進捗票（雛形）

**このファイルは雛形。実機の状態は `$AIFACTORY_WORKSPACE/docs/STATUS.md` に写して書く**（既定は `<repo>/workspace/docs/STATUS.md`。`.gitignore` 済み。ADR-0016）。VMID の割当・IP・構築日・所見のような環境固有の値はリポジトリ側には書かない。

**最終確認: YYYY-MM-DD HH:MM（どこまで済んだか。人間待ちが何か）**

このファイルは AI が更新する。着手前に「実機確認」の節を実行し、書かれている進捗と実機が一致することを確かめてから作業する。食い違ったら実機に合わせてこのファイルを直す。

## 進捗

| Step | 内容 | 状態 | 完了日 | 備考 |
|---|---|---|---|---|
| 0 | 事前確認 | ⬜ 未着手 | | ノードの pveversion / quorate / 空き RAM / 9xxx 未使用。Mac 側に `sb_ed25519` と `~/.config/sandbox/env`（`PVE_HOST` `GW_SSH` 記入） |
| 0b | GitHub App（ADR-0008） | ⬜ 未着手 | | App 名・install 先の所有アカウント。`sandbox gh-app status` で全 PJ OK |
| 0c | テナントの器（プール・ロール・ユーザー。ADR-0017） | ⬜ 未着手 | | テナント名（slug。最初の環境は main）、`SB_NET` / `SB_VMID_BASE`。`05-tenant.sh show` |
| 1 | SDN `sb` / `sbnet` / 10.77.0.0/16 | ⬜ 未着手 | | `sbnet` 10.77.0.1/16、SNAT → ホストの LAN アドレス |
| 2a | `sb-gw` LXC 作成・dnsmasq・tailscaled | ⬜ 未着手 | | eth0 の DHCP アドレス（実機の値を書く）。外向き 200 |
| 2b | 🧑 Tailscale 認証・route 承認・split DNS | ⬜ 未着手 | | 誰が承認したか。ACL（grants）に 10.77.0.0/16 を足したか。`sb-gw` の Tailscale IP |
| 2c | Mac から 10.77.0.2 到達・名前解決 | ⬜ 未着手 | | ping / `dig sb-gw.sb.internal` / `ssh sb-gw`。`SB_JUMP` を使ったかどうか |
| 2d | 制御系 LXC `sb-ctl`（9001。ADR-0017） | ⬜ 未着手 | | console / docs の URL、合言葉を渡した相手、`ctl.env` に入れた secrets の種類（値は書かない） |
| 3 | `sb-base` テンプレート（9100） | ⬜ 未着手 | | 焼いた版（Ubuntu / mise / Node / Claude Code / gh / PostgreSQL / Redis / Chrome）。検証 clone を削除したか |
| 4 | `sb-tpl-{pj}` テンプレート（911x） | ⬜ 未着手 | | PJ ごとの VMID（下表）。焼き込みスクリプトは `$AIFACTORY_WORKSPACE/projects/<pj>/provision.sh` |
| 5a | プール N 台 + スナップショット `clean` | ⬜ 未着手 | | PJ ごとの台数と VMID 範囲。全台 running・RAM 込み `clean` あり |
| 5b | `sandbox` CLI 配置・5操作の確認 | ⬜ 未着手 | | `~/.local/bin/sandbox`（実体コピー）。take→ssh→url→reset（DIRTY 消える）→release→ls |
| 5c | 通信制限（ADR-0010） | ⬜ 未着手 | | VM から LAN / ホスト / 隣 VM / tailnet へ不可、GitHub / DNS / Anthropic へ可、Mac → VM 可を実測。`clean` を firewall=1 込みで取り直したか |
| 6 | 最小 workflow で1周 | ⬜ 未着手 | | どの PJ で何周したか。所見は下 |

状態の記号: ⬜ 未着手 / 🔄 作業中 / ✅ 完了 / ⚠️ 完了したが問題あり（備考に書く）/ ⏸ 人間待ち

## 人間待ち（🧑）

| 何を | 誰が | 状態 |
|---|---|---|
| `claude setup-token` の出力を鍵プールに登録する（`sandbox keys add` か console の「鍵」画面。PJ ごとの `token set` は非推奨） | メンテナ | |
| GitHub App を作り、対象リポジトリの所有アカウント（ユーザー / org）ごとに install する（Actions: Read-only を足すと VM から `gh run list` が使える。任意で Checks: Read-only） | メンテナ | |
| Step 2b: Tailscale の認証 URL を承認し、route を Approve する | メンテナ | |
| Step 4: 対象 PJ の選定と、そのリポジトリを読める `gh auth token` | メンテナ | |

## 実機確認（着手前に必ず実行）

`"$PVE_HOST"` は `~/.config/sandbox/env` の値（`set -a; . ~/.config/sandbox/env; set +a`）。

```bash
# クラスタと対象ノード
ssh "$PVE_HOST" 'hostname; pvecm status | grep -E "Quorate|Total votes"; free -g | awk "/Mem:/{print \$7\" GB avail\"}"'
# 作成済みの sandbox 資源（何も無ければ Step 0 以前）
ssh "$PVE_HOST" 'pvesh get /cluster/sdn/zones --output-format json | jq -r ".[].zone"'          # sb があれば Step 1 済み
ssh "$PVE_HOST" 'pct list | grep -E "^\s*9000"'                                                  # sb-gw があれば Step 2a 済み
ssh "$PVE_HOST" 'qm list | grep -E "^\s*9(1|2)[0-9]{2}"'                                         # 9100 / 911x / 92xx
ssh "$PVE_HOST" 'for i in $(qm list | awk "/sb-/{print \$1}"); do echo "== $i"; qm listsnapshot $i; done'  # clean があれば Step 5a 済み
# Mac 側
ls -l ~/.ssh/conf.d/aifactory/ ~/.config/sandbox/ 2>/dev/null
ping -c1 -W1 10.77.0.2 >/dev/null 2>&1 && echo "tailnet → sb-gw OK (Step 2 済み)" || echo "sb-gw 未到達"
dig +short sb-gw.sb.internal
command -v sandbox && sandbox ls
```

## 判定の目安

| 実機の状態 | 進捗票での位置 |
|---|---|
| zone `sb` 無し | Step 0 または未着手 |
| zone あり、pct 9000 無し | Step 1 完了 |
| 9000 あり、Mac から 10.77.0.2 に ping 不可 | Step 2b（人間待ち） |
| ping 可、qm 9100 無し | Step 2 完了 |
| 9100 が template、911x 無し | Step 3 完了。PJ 選定待ちの可能性 |
| 92xx に `clean` スナップショット | Step 5a 完了 |

## PJ 一覧（YYYY-MM-DD）

PJ 定義は `$AIFACTORY_WORKSPACE/projects/<pj>/`（同梱サンプルは `examples/projects/kumitate`）。1 PJ = 1 テンプレート（911x）+ プール N 台（92xx）。

| PJ | リポジトリ / ブランチ | スタック | テンプレート | プール（VMID / IP） | 焼き込み時のゲート | リポジトリ側の指摘事項（sandbox 起因ではない） |
|---|---|---|---|---|---|---|
| kumitate（例） | akkijp/kumitate / develop | pnpm 10 + Next.js + Postgres 16(pgvector) + drizzle | 9110 ⬜ | 9201〜9203 / 10.77.1.1〜3 | tokens / typecheck / lint / test / test-dom | 時間依存テスト 2 件（フィクスチャが 8 月固定）→ info 扱い |
| `<pj>` | `owner/repo` / `main` | | 911x ⬜ | 92xx / 10.77.1.xx | | |

VM は各 8GB / 4 vCPU（`VM_MEMORY` / `VM_CORES`）。アプリは各 VM の :3000（`APP_PORT`）。ログイン情報（seed のテストユーザー）は PJ ごとにここへ書く。

## GitHub App（YYYY-MM-DD）

- App: `<slug>`（id、public / private、owner）。権限 Contents RW / Pull requests RW / Metadata R（+ 任意で Actions R / Checks R）。鍵は Mac `~/.config/sandbox/gh-app/`
- install: 所有アカウントごとに all / selected のどちらか
- 確認済み: `take <pj>` → VM 内で使い捨てブランチ push → 削除 → `gh api repos/...` OK。`gh-app refresh` で貸出中 VM のトークンが更新されること
- launchd `com.aifactory.sandbox.gh-refresh`（45 分ごと）を `~/Library/LaunchAgents` に登録済みか

## 実機の補足（YYYY-MM-DD）

構築中に踏んだ環境固有のこと（DHCP で付いたアドレス、tailnet の ACL 形式、LVM-thin の WARNING、`SB_JUMP` を使った期間など）を書く。枠組み側の教訓は `BUILD.md` / `OPERATIONS.md` / `templates/README.md` へ戻す。既知のもの:

- 通信制限（Step 5c）を貸出中の VM に適用すると、その `clean` に作業状態が写り込む。`LENT` で必ず除外する
- sb-gw の FORWARD DROP は `--ctstate NEW` 限定。無条件にすると Mac → VM の戻りパケットまで落ちて疎通が切れる
- `local-lvm` は thin なので `qm clone` / `snapshot` のたびに "Sum of all thin volume sizes exceeds..." の WARNING が出る。実使用は `lvs pve/data` の data% を見る
- Tailscale は route 承認だけでは届かない。ACL が grants 形式で subnet 宛てを個別許可している tailnet では `10.77.0.0/16` を grant に足す（BUILD.md Step 2b の 3）

## 1周の所見（Step 6、YYYY-MM-DD 実施）

対象 PJ と、使った workflow（`workflow/bin/run <wf> <pj> <ticket>`）を書く。

| run | 内容 | モデル | agent | ゲート | 結果 |
|---|---|---|---|---|---|
| `<pj> <ticket>` | | | 秒 | 分 | PR # |

観察（枠組みに戻す価値のあるものは ADR か各 README へ）:
- take から `claude` 起動までの秒数（RAM 込みスナップショットなら VM の起動待ちは無い。実績 約 10 秒）
- 時間の大半はゲートか agent か
- base で既に赤いゲートを agent に戻していないか（`known_red_gates`）
- 自動コミットが未追跡ファイルを拾っていないか（`git add -u`）
- GitHub App トークンの 1 時間失効を run が跨いでいないか（runner は code step 前に払い出し直す）
- 失敗時に VM を巻き戻す前に成果を退避したか（`sandbox/<task>-<wf>-wip` へ push）
- 足りなかった操作（例: スクショを Mac に持ってくる、ログを取り出す）
- 契約（take / ssh / url / reset / release）は workflow から見て足りたか

## 変更履歴
- YYYY-MM-DD: （何を実機に構築したか。どの Step まで）
