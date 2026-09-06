# sandbox 運用書

構築後の日常操作と、壊れたときの直し方。構築は `BUILD.md`。

以下、`"$PVE_HOST"` は `~/.config/sandbox/env` に書いた Proxmox ホストの ssh エイリアス（例: `pve1`）。手打ちするときは `set -a; . ~/.config/sandbox/env; set +a` で読み込んでおく。

## 日常の5操作

```bash
sandbox ls                      # 貸出状況
sandbox take kumitate 013       # 空き VM を task 013 に貸す（DNS: task-013.sb.internal）
sandbox ssh 013                 # 中に入る（dev ユーザー）
sandbox ssh 013 'git status'    # コマンドだけ実行
sandbox url 013                 # http://task-013.sb.internal:3000
sandbox reset 013               # clean に巻き戻す（貸出は継続。env も消えるので take し直しは不要、CLI が再注入する）
sandbox release 013             # 巻き戻して返却
```

cmux から使うときは surface で `sandbox take … && sandbox ssh …` を1行で打つ。surface = ssh セッション = 1エージェント。

## トークンの管理（PJ ごと。ADR-0006）

```bash
sandbox token set kumitate            # Claude の長期トークンを対話入力（エコー無し）→ ~/.config/sandbox/pj/kumitate.env
sandbox token set myapp gh            # GitHub トークン（GitHub App 未設定時のフォールバック）
sandbox token show myapp              # 今どれが効いているか（マスク表示）
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

## 障害と対処

| 症状 | 見るところ | 対処 |
|---|---|---|
| Mac から `task-xxx.sb.internal` が解けない | `dig sb-gw.sb.internal`、Tailscale の split DNS 設定 | split DNS が消えていないか。`ssh root@10.77.0.2 systemctl status dnsmasq` |
| 10.77.0.2 に ping 不可 | Tailscale 管理コンソールで `sb-gw` の route が Approved か。Approved でも不可なら `pct exec 9000 -- journalctl -u tailscaled -n 20` に `Drop: … no rules matched` が出ていないか（= tailnet **ACL** に `10.77.0.0/16:*` 宛て accept が無い） | `pct exec 9000 -- tailscale status`。落ちていれば `pct start 9000`。**急ぎなら** `~/.config/sandbox/env` に `SB_JUMP=pve1`（`PVE_HOST` と同じ値）を入れると CLI が Proxmox ホスト経由（ProxyJump）で VM に入る（ホストに ssh できる Mac から。URL は IP 直打ち） |
| VM に ssh 不可 | `qm status 92NN`、`qm agent 92NN network-get-interfaces` | `qm start`。起動していれば `qm terminal 92NN` でシリアルから見る |
| `reset` が失敗 | `qm listsnapshot 92NN` に `clean` があるか | 無ければその VM は破棄して `40-pool.sh` で作り直す |
| VM から外に出られない | `ssh "$PVE_HOST" 'iptables -t nat -S | grep 10.77'`、`pve-firewall status` | SDN を再適用 `pvesh set /cluster/sdn`。firewall で落ちている場合は `/etc/pve/firewall/cluster.fw` の group sandbox を確認（LAN / 他 VM / tailnet 宛ては仕様で不可。ADR-0010） |
| Mac から VM に届かない（firewall 有効化後） | VM の `/etc/pve/firewall/<vmid>.fw` と `qm config <vmid> | grep firewall` | `sandbox/proxmox/run.sh 50-firewall.sh` を再実行。プールは clean スナップショットに firewall=1 が含まれている必要がある |
| Claude Code が認証エラー | VM 内 `env | grep CLAUDE_CODE_OAUTH_TOKEN` | Mac で `sandbox token set <pj>` を更新して `sandbox reinject`（トークン期限切れは `claude setup-token` 再実行） |
| Proxmox ノードが落ちた | `ssh "$PVE_HOST"` 不可、`pvecm nodes` | ノードの電源投入（遠隔でできるかは環境次第。できない環境では人間の物理操作）。プールは onboot=0 なので手で `qm start` |

## 定期メンテ
- 月1回: base テンプレートの OS 更新（上記「base 層」）。頻繁にやると PJ 層の作り直しが負担なので月1
- `claude setup-token` のトークンは有効期限がある。切れたら人間待ちに戻る。`$AIFACTORY_WORKSPACE/docs/STATUS.md` の人間待ち表に期限を書いておく
