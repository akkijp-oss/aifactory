# sandbox 運用書

構築後の日常操作と、壊れたときの直し方。構築は `BUILD.md`。

以下、`"$PVE_HOST"` は `~/.config/sandbox/env` に書いた Proxmox ホストの ssh エイリアス（例: `pve1`）。手打ちするときは `set -a; . ~/.config/sandbox/env; set +a` で読み込んでおく。

## どこから操作するか（ADR-0017）

| 立場 | どこで | Proxmox への口 |
|---|---|---|
| 貸出先（テナントの利用者） | 制御系 LXC の中（`ssh aifactory@ctl.<t>.sb.internal`）か、そのコンソール `http://ctl.<t>.sb.internal:8765/`（docs は `/docs/`） | API モード（`PVE_API_TOKEN`。自分のプールだけ） |
| メンテナ（ホスト管理者） | 手元の Mac。別テナントは `SB_TENANT=<t>` を付ける（`~/.config/sandbox/tenants/<t>.env`） | ssh モード（`PVE_HOST` に root）。構築スクリプト（`proxmox/run.sh`）はこちらだけ |

**1 テナントに制御系は 1 つ**。貸出台帳（`~/.config/sandbox/state.json`）は制御系ごとに別なので、Mac と制御系 LXC の両方から同じプールに `take` すると二重貸出になる。制御系 LXC に寄せたら Mac 側では `take` しない（`ls` は読むだけなので可）。
同じ制御系の中でなら `take` は安全で、空き VM の選定と台帳への予約は `state.json.lock`（`flock`。無い環境は `state.json.lock.d`）で直列化される。`kb run` を同時に何本立てても同じ VM が 2 つの task に貸し出されることはない（2026-09 の 234）。プールが尽きたときは後発が「空きなし」で失敗する。

制御系 LXC の常駐は systemd: `systemctl status aifactory-console aifactory-gh-refresh.timer aifactory-idle-stop.timer`、ログは `journalctl -u aifactory-console -f`。コードの更新は下記の `bin/ctl-update` 1 本。

| 常駐 | 何をするか | 間隔 |
|---|---|---|
| `aifactory-console` | コンソールと MCP と docs | 常時 |
| `aifactory-gh-refresh.timer` | 貸出中の VM の `GH_TOKEN` を払い出し直す（ADR-0008） | 45 分 |
| `aifactory-idle-stop.timer` | 使われていないプール VM を止める（`sandbox idle-stop`。ADR-0033） | 15 分 |

登録・解除は `sandbox/bin/install.sh --systemd` / `--remove`（両方の timer をまとめて扱う）。

## 制御系にコードを配備する（`bin/ctl-update`）

main にマージしただけでは制御系のコンソール・MCP・runner には効かない（VM は GitHub から clone するので効く）。LXC の中で 1 本走らせる。

```bash
cd ~/aifactory
bin/ctl-update                  # origin/main の最新へ（fetch → 早送り → CLI → docs → console restart → 疎通 → 版）
bin/ctl-update --ref v1.2.0     # 任意の版へ（sha / tag / origin/<branch>。detached になる）
bin/ctl-update --no-docs        # website を直していないとき（mkdocs build を飛ばす）
bin/ctl-update --dry-run        # 何をするかだけ見る
```

- `sudo` は頭で 1 回だけ聞かれる（systemd の unit 更新と restart）。console の bind 先は今の unit の `CONSOLE_HOST` / `CONSOLE_PORT` を引き継ぐ
- 最後に console（`/api/config`）・MCP（`initialize`）・runner（python3 の yaml / jsonschema）の疎通と、配備した版の `git log -1` が出る
- `._*` / `.DS_Store` は走るたびに消す（種別・役割の候補に `._bug` が混ざる原因。218 / 247）

**制御系の `~/aifactory` は clean な checkout で運用する。** 汚れていると `ctl-update` は配備せずに止まる。テナント構築を `AIFACTORY_LOCAL_TREE=1`（未 push の tree を被せる。`25-control-lxc.sh`）でやった LXC はこの状態なので、一度だけ入れ替える:

```bash
mv ~/aifactory ~/aifactory.pre-clean-$(date +%Y%m%d)
git clone <origin の URL> ~/aifactory
mv ~/aifactory.pre-clean-*/workspace ~/aifactory/   # workspace をリポジトリ内に置いていたときだけ（ADR-0016）
cd ~/aifactory && bin/ctl-update
```

退避先にしか無い変更は、そこから拾って PR にする（この tree には戻さない）。落ち着いたら `rm -rf ~/aifactory.pre-clean-*`。

## 日常の5操作

```bash
sandbox ls                      # 貸出状況
sandbox take kumitate 013       # 空き VM を task 013 に貸す（DNS: task-013.sb.internal）
sandbox ssh 013                 # 中に入る（dev ユーザー）
sandbox ssh 013 'git status'    # コマンドだけ実行
sandbox url 013                 # http://task-013.sb.internal:3000
sandbox reset 013               # clean に巻き戻す（貸出は継続。env も消えるので take し直しは不要、CLI が再注入する）
sandbox release 013             # 巻き戻して返却（巻き戻しに失敗したら非0で終わり、台帳には残る）
sandbox release 013 --force     # 手で直した VM を巻き戻さずに返す（台帳からだけ消す）
```

cmux から使うときは surface で `sandbox take … && sandbox ssh …` を1行で打つ。surface = ssh セッション = 1エージェント。

## トークンの管理（PJ ごと。ADR-0006）

```bash
sandbox token rotate                  # 期限切れの差し替えはこれ 1 本（制御系で）。global・鍵を持つ全 PJ・ctl.env を対話入力 1 回で更新し、
                                      #   更新箇所を一覧、aifactory-console を restart、貸出中 VM に reinject --all まで（ADR-0029）
sandbox token set kumitate            # 1 PJ だけ（初期登録・rotate 後に別アカウントへ戻すとき）→ ~/.config/sandbox/pj/kumitate.env
sandbox token set myapp gh            # GitHub トークン（GitHub App 未設定時のフォールバック）
sandbox token show myapp              # 今どれが効いているか（マスク表示・発行からの日数・このホストが制御系か）
sandbox reinject --all                # 貸出中の VM 全部に差し替え後の値を再注入（巻き戻しなし）。VM 内の claude は再起動
SANDBOX_CLAUDE_TOKEN=xxx sandbox take kumitate 021      # 一回限りの上書き（シェルの CLAUDE_CODE_OAUTH_TOKEN は無視される）
```

優先順位: `SANDBOX_CLAUDE_TOKEN` / `SANDBOX_GH_TOKEN`（一回限り） > `pj/<pj>.env` > `env`。`token show <pj>` に出どころが出る。`take` / `reset` は task の PJ を覚えているので、以後の操作で PJ を指定し直す必要はない。

### GitHub の push / PR 権限（GitHub App。ADR-0008）

```bash
sandbox gh-app status           # App の設定・install 済みの所有アカウント・PJ ごとのトークン可否
sandbox gh-app token kumitate   # そのリポジトリだけに効く 1 時間トークンを表示（手で git push するときなど）
sandbox gh-app refresh          # 貸出中の VM 全部の GH_TOKEN を払い出し直す（launchd が 45 分ごとに実行）
```

- 対象リポジトリを増やしたら `https://github.com/apps/<slug>/installations/new` で install 先に追加し、`pj/<pj>.env` に `GH_REPO` を書く
- VM 内で `git push` が 401 になったらトークン切れ。`sandbox gh-app refresh` で復旧（launchd が止まっていないか `launchctl list | grep aifactory`。exit 126 なら `~/.local/bin/sandbox` がシンボリックリンクになっている＝`sandbox/bin/install.sh` で実体コピーに）
- workflow runner は code step（gates / pr / merge）の直前に自分でトークンを払い出し直すので、launchd が止まっていても 1 時間超の run は動く（84 分の run でトークンが失効して merge が失敗した教訓）
- App を作り直すときは `~/.config/sandbox/gh-app/` を消して `sandbox/bin/gh-app-setup`

## プールを増やすときの注意
- `40-pool.sh` は `SB_POOL_BASE`+1（既定 9201）から空き VMID を探して採番する。**2 つの PJ のプール作成を同時に走らせない**（同じ VMID を同時に掴むと後の方の `qm clone` が失敗する。2 PJ を並行させると番号が交互に混ざる実績あり。動作には支障なし）
- 1 PJ = 3 台 × 8GB が目安。ノードの `free -g` と `lvs pve/data` を見てから増やす

## take が「空きなし」で失敗したら
1. `sandbox ls` で貸出中の task を見る。終わっているものは `release`
2. 全部使用中なら `proxmox/run.sh 40-pool.sh {pj} 1` で1台足す（VMID と IP は自動採番。上限は 9299 / 10.77.1.99）

## VM の中で守ること
- 作業は `/home/dev/app`（PJ リポジトリ）で行う。ブランチを切って push する。**VM 内の変更は release で消える**
- 成果物（スクショ・ログ）を残したいものは `git push` か `sandbox ssh <id> 'cat …' > local` で取り出す
- `sudo` は使えるが、環境を変えたら release 後に消える。恒久的に必要なものはテンプレートに焼く（下記）

## テンプレートの更新

### PJ 層だけ（依存の追加・seed の変更）
1. `ssh "$PVE_HOST" 'qm clone 9110 9111 --name sb-tpl-{pj}-next --full 1'` で full clone
2. 起動して中で変更 → lint / test が緑 → 後片付け（`templates/README.md` の「焼く前」節）→ 停止
3. `qm template 9111`。プールを作り直す: 各 92NN を `release` → `qm destroy 92NN --purge` → `40-pool.sh {pj} 3`（クローン元 VMID は環境変数 `TPL_VMID=9111`）
4. 問題なければ旧 9110 を削除し、9111 を 9110 に揃える必要はない（VMID は `TPL_VMID` で指定できる）。`$AIFACTORY_WORKSPACE/docs/STATUS.md` にどれが現行か書く

### base 層（OS パッケージ・ツール）
base を直したら PJ 層も作り直しになる。`30-base-template.sh` → `31-provision-base.sh` → `32-pj-template.sh` → `40-pool.sh` の順で、新しい VMID（9101 / 9111 / 92NN）で一式作り、切り替える。

## VM が `stopped` になっている

`sandbox ls` の STATUS が `stopped` なのは、たいてい**故障ではなく節電**です。`sandbox idle-stop` は 2 段で決めます: 貸し出されておらず、最後に使われてから 24 時間（既定）経ったプール VM を**停止候補**にし、候補が 10 台（既定。足切り）を超えたぶんだけ、最終利用の古い順に止めます。候補が 10 台以下なら 1 台も止めません。表の下に `[idle-stop] N 台が節電で停止中（次の take で起動、+30〜60 秒）` と出ていれば止まった VM、`[idle-stop] N 台が停止候補` と出ていれば候補のまま起動している VM です（ADR-0035）。

**手で起こす必要はありません**。次の `take` が自動で起動し、ssh が上がるまで待って `[start] vm <vmid>: 停止中だったので起動した（N 秒）` を出します（起こすためだけのコマンドは用意していません。ADR-0033）。

```bash
sandbox idle-stop --dry-run     # 何が候補になり、何が止まる判定になるか、止めずに見る
sandbox idle-stop --hours 6     # この 1 回だけ候補の条件を 6 時間に
sandbox idle-stop --keep 0      # この 1 回だけ足切りなし（候補を全部止める）
```

常時起動にしたい PJ は `~/.config/sandbox/pj/<pj>.env` に `SB_IDLE_STOP_HOURS=0`、全体で止めたくないときは `~/.config/sandbox/env` に `SB_IDLE_STOP_HOURS=0` を書きます。足切りの台数は `SB_IDLE_STOP_KEEP`（全体のみ）で変えます。最終利用は `~/.config/sandbox/last-used.json`、直近の判定結果は `~/.config/sandbox/idle-stop.json` にあります。

`stopped` のまま次の `take` でも起動してこないなら故障です。`journalctl -u aifactory-idle-stop` と Proxmox 側を見てください。

## 障害と対処

| 症状 | 見るところ | 対処 |
|---|---|---|
| Mac から `task-xxx.sb.internal` が解けない | `dig sb-gw.sb.internal`、Tailscale の split DNS 設定 | split DNS が消えていないか。`ssh root@10.77.0.2 systemctl status dnsmasq` |
| 10.77.0.2 に ping 不可 | Tailscale 管理コンソールで `sb-gw` の route が Approved か。Approved でも不可なら `pct exec 9000 -- journalctl -u tailscaled -n 20` に `Drop: … no rules matched` が出ていないか（= tailnet **ACL** に `10.77.0.0/16:*` 宛て accept が無い） | `pct exec 9000 -- tailscale status`。落ちていれば `pct start 9000`。**急ぎなら** `~/.config/sandbox/env` に `SB_JUMP=pve1`（`PVE_HOST` と同じ値）を入れると CLI が Proxmox ホスト経由（ProxyJump）で VM に入る（ホストに ssh できる Mac から。URL は IP 直打ち） |
| VM に ssh 不可 | `qm status 92NN`、`qm agent 92NN network-get-interfaces` | `qm start`。起動していれば `qm terminal 92NN` でシリアルから見る |
| `reset` が失敗 | `qm listsnapshot 92NN` に `clean` があるか | 無ければその VM は破棄して `40-pool.sh` で作り直す |
| `release` / `reset` が「巻き戻しに失敗」で止まる | `sandbox ls`、`qm config 92NN | grep lock` | 台帳は残っているので少し待って再実行（既定で 3 回・10 秒間隔まで自動再試行。`SB_ROLLBACK_TRIES` / `SB_ROLLBACK_WAIT` で伸ばせる）。ロックが残り続けるなら Proxmox 側で task を確認。手で直したら `sandbox release <task> --force` |
| VM から外に出られない | `ssh "$PVE_HOST" 'iptables -t nat -S | grep 10.77'`、`pve-firewall status` | SDN を再適用 `pvesh set /cluster/sdn`。firewall で落ちている場合は `/etc/pve/firewall/cluster.fw` の group sandbox を確認（LAN / 他 VM / tailnet 宛ては仕様で不可。ADR-0010） |
| Mac から VM に届かない（firewall 有効化後） | VM の `/etc/pve/firewall/<vmid>.fw` と `qm config <vmid> | grep firewall` | `sandbox/proxmox/run.sh 50-firewall.sh` を再実行。プールは clean スナップショットに firewall=1 が含まれている必要がある |
| Claude Code が認証エラー | VM 内 `env | grep CLAUDE_CODE_OAUTH_TOKEN`、`sandbox token show <pj>` の発行日数 | 制御系で `claude setup-token` → `sandbox token rotate`（global・全 PJ・ctl.env の更新、console restart、`reinject --all` まで 1 コマンド。VM 内の claude は再起動） |
| Proxmox ノードが落ちた | `ssh "$PVE_HOST"` 不可、`pvecm nodes` | ノードの電源投入（遠隔でできるかは環境次第。できない環境では人間の物理操作）。プールは onboot=0 なので手で `qm start` |

## テナントの運用（ADR-0017）

- 名前はすべて規則（`sandbox/README.md` の「テナント」節）。2026-09-06 以前の名前で作った環境は `07-migrate-naming.sh` で移す
- 新しい貸出先: `BUILD.md` の Step 0c → 1 → 2a → 2d → 2b（貸出先の tailnet で `tailscale up`）→ 3 → 4 → 5 → 5c を `SB_TENANT=<t>` で。所要は 1 テナントあたり数時間（テンプレート焼き込みが大半）
- 貸出先に渡すもの: 制御系 LXC への ssh（鍵を `authorized_keys` に足す）、コンソールの合言葉（`ctl.env` の `CONSOLE_TOKEN`。貸出先が自分で変えてよい）、docs の URL。渡さないもの: ホストの root、他テナントの何か
- API トークンの作り直し（漏えい・紛失）: `SB_TENANT=<t> sandbox/proxmox/run.sh 25-control-lxc.sh`（再実行で発行し直して LXC に書く）
- 貸出先が壊した制御系: LXC を `pct destroy` して `25-control-lxc.sh`。workspace（チケット・記録）は LXC の中なので、必要なら先に `pct exec … tar` で退避
- テナントの片付け: プール VM を `release` → `qm destroy` → テンプレート → `25` / `20` の LXC → `pveum user delete` / `pveum pool delete` → SDN（`10-sdn.sh` の巻き戻し）。まだスクリプト化していない（ADR-0017 の未決）
- 症状「制御系から `sandbox ls` が `Proxmox API … が失敗`」: LXC から `curl -k https://<SB_NET>.0.1:8006/api2/json/version` が通るか（firewall group `<group>-ctl` の OUT が自テナントの /16 を許可しているか）、トークンの有効期限（`pveum user token list`）

## 定期メンテ
- 月1回: base テンプレートの OS 更新（上記「base 層」）。頻繁にやると PJ 層の作り直しが負担なので月1
- `claude setup-token` のトークンは有効期限がある。切れたら人間待ちに戻る。`sandbox token show` の「発行から N 日」で切れる前に気づき、制御系で `sandbox token rotate`
