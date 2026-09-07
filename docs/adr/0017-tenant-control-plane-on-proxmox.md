# ADR-0017: 制御系も Proxmox 上に置き、テナント（貸出先の組織）ごとに網・制御系・権限を分ける

日付: 2026-09-06 / 状態: 採用 / 決定者: メンテナ（「console や docs も含めて全部 Proxmox 上で完結させたい。複数のサンドボックスのネットワーク環境を分けて、異なる組織に貸し出す前提で」）

## 状況
- v1 の物理配置は「制御系（kanban / glue / runner / console / MCP / workspace / 秘密情報）は Mac、実行系（VM）は Proxmox」だった（ADR-0013、website の「全体の構成」）。Mac が落ちれば run も console も止まり、常駐は launchd という macOS 固有の仕組みに依っていた
- sandbox の網は 1 つ（SDN zone `sb` / vnet `sbnet` / `10.77.0.0/16`）で、名前も番号も固定（`sb-gw` 9000、`sb-base` 9100、`sb-<pj>-NN` 92xx、firewall group `sandbox`）。2 つ目の環境を同じホストに作る手段が無かった
- `sandbox` CLI は Proxmox ホストに **root で ssh** して `qm` を叩く。工場を別の組織に貸すとき、その組織の制御系にホストの root を渡すわけにはいかない
- 貸出先ごとに「見えるもの・触れるもの」を切り分けたい: 自分の VM だけ、自分の網だけ、自分の秘密情報だけ

## 決定
1. **テナント**という単位を置く（`SB_TENANT`、slug は `[a-z0-9]{1,6}`）。1 テナント = 1 貸出先 = 独立した sandbox 環境。最初の環境も特別扱いせず slug `main` を持つ（当初は `default` を互換扱いにしたが、同日夜に撤回。下の追記）
2. テナントごとに次を **別に** 持つ。導出は `sandbox/proxmox/_tenant.sh` に 1 つ（`SB_TENANT` / `SB_NET` / `SB_VMID_BASE` から）
   - **命名規則: すべての名前に `sb` と `<t>` を含める**（メンテナ決定 2026-09-07）
   - SDN: zone `sb<t>` / vnet `vn<t>`（英数字 8 文字以内）/ subnet `SB_NET.0.0/16`（テナントごとに別の /16）。L2 も IP 空間も重ならない
   - VMID 帯: `SB_VMID_BASE`（1000 刻み）。gw = +0、ctl = +1、base = +100、PJ テンプレート = +110..、プール = +200..
   - 名前: `sb-<t>-gw` / `-ctl` / `-base` / `-tpl-<pj>` / `-<pj>-NN`。DNS ドメイン `<t>.sb.internal`（`gw.` / `ctl.` / `task-<id>.` / `<pj>-NN.`）。Tailscale ホスト名 `sb-<t>-gw`
   - firewall: セキュリティグループ `sb-<t>`（VM 用）と `sb-<t>-ctl`（制御系用）。`cluster.fw` の中では自分のグループの節だけ書き換える
   - Proxmox の **リソースプール** `sb-<t>` とユーザー `sb-<t>@pve`（トークン `ctl`）、ロール `AifactorySandbox`（VM.Audit / VM.PowerMgmt / VM.Snapshot / VM.Snapshot.Rollback / Pool.Audit）。ACL はそのプールにだけ
   - ゲートウェイ LXC `<prefix>-gw`: **貸出先の組織の tailnet** に入れる（`tailscale up` はその組織のアカウントで）。subnet router + dnsmasq
3. **制御系 LXC `<prefix>-ctl`** をテナントの vnet に 1 台置く（`SB_NET.0.3`。LAN に足を出さない）。中身: aifactory の checkout、workspace、`sandbox` CLI、Web コンソール（systemd、tailnet 向けに bind）、ドキュメントサイト（`mkdocs build` の出力をコンソールが `/docs/` で配信）、GitHub App トークン更新の timer、runner が使う python3 / gh / claude / ssh。**Mac 側には何も要らない**（ブラウザと ssh だけ）。既存の Mac 運用（launchd）も同じ CLI で動くが、1 テナントに制御系は 1 つにする（貸出台帳が別になるため）
4. `sandbox` CLI に **Proxmox API モード** を足す。`PVE_API_TOKEN`（テナントのユーザーのトークン。`privsep 0`）があれば `qm` の代わりに REST API（`/pools/<pool>`、`snapshot`、`rollback`、`status/start`）を使う。制御系はこのモードで動き、ホストの root は持たない。ssh モード（`PVE_HOST`）はホスト管理者向けにそのまま残す。呼ぶ側は `pve_vms` / `pve_has_clean` / `pve_lock` / `pve_rollback` / `pve_status` / `pve_start` の 6 つだけを使う
5. コンソールは **合言葉（`CONSOLE_TOKEN`）が無い限り 127.0.0.1 以外に bind しない**。合言葉があるときは `/?token=` で cookie を焼くか `Authorization: Bearer` で通す。到達性の境界（テナントの tailnet + firewall）に合言葉を 1 枚重ねる
6. 構築はホスト管理者（メンテナ）が `SB_TENANT=<t> sandbox/proxmox/run.sh …` で行う（`05-tenant.sh` → `10-sdn.sh` → `20-gateway-lxc.sh` → `25-control-lxc.sh` → `30`〜`50`）。API トークンは `25-control-lxc.sh` が発行して制御系の `~/.config/sandbox/env` に直接書く（画面に出さない。再実行で作り直す）。制御系の ssh 鍵は `run.sh` が拾って以後のテンプレートと gw に入れる
7. 秘密情報は **テナントの制御系の中** に置く（`~/.config/sandbox/`、`~/.config/aifactory/ctl.env`）。貸出先が自分で入れる（Claude の長期トークン、GitHub App、コンソールの合言葉）。メンテナはホストの root と各テナントの ssh 鍵を持つが、貸出先のトークンは持たない

## 理由
- **Proxmox で完結**: 制御系が LXC なら、Mac の電源やログイン状態に左右されない。systemd で常駐し、落ちれば再起動する。docs も同じ LXC から配れるので「工場の使い方」を貸出先に渡すのに別ホスティングが要らない
- **隔離の層を 3 つ重ねる**: 網（別 vnet + firewall で RFC1918 全体を DROP = 他テナントの網にも出られない）、権限（API トークンがプール限定。制御系が乗っ取られても他テナントの VM は見えない・触れない）、秘密情報（制御系の中だけ。テナント間で共有しない）。どれか 1 つが破れても他が残る
- **API モードにした理由**: ssh + `qm` を仲介する独自ブローカーを書くより、Proxmox 標準の権限モデル（プール + ロール + トークン）に乗るほうが監査も更新も楽。必要な操作は一覧・状態・起動・巻き戻しだけで、API で足りる
- **最初の環境も規則に載せた理由**（2026-09-07 追記）: 裸の `sb.internal` を残すと、同じ tailnet に複数テナントが載ったとき split DNS の `sb.internal` が `demo.sb.internal` の問い合わせまで拾いうる。名前を見れば所属テナントが分かる状態にする。既存環境の移行は `07-migrate-naming.sh`（VM を止めずに NIC のブリッジを付け替え、改名、プール移動、`clean` 取り直し）
- **gw を貸出先の tailnet に入れる理由**: 貸出先が自分の端末から自分の環境に届く経路を、メンテナの tailnet を経由せずに作れる。ACL・承認も貸出先が自分で管理する
- **合言葉を足した理由**: tailnet の中にも多数の端末がある。無認証のまま外に出さないという ADR-0013 の線は守り、最小の認証を足す。本格的な認証（OIDC 等）は必要になったら別 ADR

## 結果（トレードオフ）
- 良い: 1 ホストに複数の貸出先環境を並べられる。貸出先はブラウザ（コンソール + docs）と ssh だけで工場を使える。ホストの root は誰にも渡さない
- 良い: Mac 運用と LXC 運用が同じスクリプト・同じ CLI で動く。名前から所属テナントが読める
- 悪い: 新しい可動部が 3 つ（テナント導出、API モード、制御系 LXC）。構築手順が 2 ステップ増える（`05` と `25`）
- 悪い: 制御系 LXC は tenant の網にしか居ないので、貸出先が gw の tailnet 承認を終えるまでコンソールに届かない（ホスト管理者は `pct exec` か ProxyJump で入れる）
- 悪い: 制御系にも `claude`（intake 用）と `gh`（runner の `gh pr view` 用）のトークンが要る。貸出先が `ctl.env` に入れる
- 未決: 制御系 LXC の更新（`git pull` + 再起動）の自動化。テナント削除の手順（プール・SDN・LXC・ユーザーの片付け）。RAM / ディスクのテナント別上限（Proxmox のプール単位の制限は無いので、運用で決める）。共有ストレージ + 複数ノード

## 状態
採用。スクリプト（`sandbox/proxmox/_tenant.sh` / `05-tenant.sh` / `25-control-lxc.sh` / `45-pool-keys.sh`、他の番号付きスクリプトのテナント対応）、`sandbox` CLI の API モード、コンソールの合言葉と `/docs/`、systemd unit、`glue/bin/dispatch` の貸出数の読み方（VM 名でなく state.json）まで実装。

実機（2026-09-06、当時は `default` と呼んでいた `main` テナント）: `05-tenant.sh` でプール `aifactory` に既存 33 台を取り込み、`25-control-lxc.sh` で `sb-ctl`（9001、10.77.0.3）を作成。踏んだこと: (1) 制御系の checkout は公開 main なので未 push の変更が入らない → `run.sh AIFACTORY_LOCAL_TREE=1` で作業ツリーを送る口を足した (2) firewall group `sandbox-ctl` を `50-firewall.sh` で定義するまで LXC への IN が全部落ちる → 手順は 25 のあと 50 (3) API の証明書の SAN はノード名だけで SDN 側 IP が無い → `PVE_API_URL=https://<node>:8006` + `PVE_API_RESOLVE` (4) 既存プールに制御系の鍵が無い → `45-pool-keys.sh`（guest agent で追記 + `clean` 取り直し）。制御系から API モードの `sandbox ls` / `take` / `ssh` / `reset` / `release`、コンソール（合言葉・cookie）、`/docs/` を確認。コンソールの API から `kb run`（research、aifactory PJ）を起動し、take → agent 2 step → 成果物回収 → release まで 161 秒で無人完走（Mac は一切関与しない）。2 つ目のテナント `demo`（10.78.0.0/16、VMID 8000 番台、`*.demo.sb.internal`）も同日に構築: `05` → `10` → `20` → `25` → `50` → `30` + `31` → `32` + provision（aifactory）→ `40`（1 台）。demo の制御系からコンソール API 経由で research を起動し 114 秒で無人完走。demo ↔ default の制御系・VM・LAN への到達が両方向とも DROP されることを実測（自テナントの gw DNS とインターネットは可）。踏んだこと: `20` は CT 起動前に鍵を追記して落ちる（修正）／制御系の名前解決は gw に向くので gw が止まっていると `25` の apt が詰まる（手順に明記）／`31` の verify が `dev` DB 無しで落ちる（`createdb` 追加）／`40` は PJ の初回で `pipefail` に落ちる（修正）。demo の gw の `tailscale up`（貸出先のアカウント）は未

## 追記（2026-09-07 命名規則）
- メンテナの判断で「`demo.sb.internal` の形を全テナントに揃える。Proxmox の zone / vnet などにも命名規則を持たせる」。既存環境は slug `main` に改名して規則に載せた（`07-migrate-naming.sh`。zone `sb` → `sbmain`、vnet `sbnet` → `vnmain`、pool `aifactory` → `sb-main`、`sb-<pj>-NN` → `sb-main-<pj>-NN`、`sb.internal` → `main.sb.internal`。demo も `zdemo` / `vdemo` / `aifactory-demo` から `sbdemo` / `vndemo` / `sb-demo` へ）
- MCP は user スコープの `aifactory`（`console/bin/mcp-remote` で制御系に ssh 越し stdio）に寄せ、Mac 側の運用（launchd、Mac の workspace）は止めた。正本は制御系 LXC の workspace
