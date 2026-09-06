# 日々の運用

このページで分かること: 定期的に必要になる作業（トークン、プール、テンプレート、firewall）と、障害時の見るところ。正本は `sandbox/OPERATIONS.md` と `sandbox/STATUS.md`。

## 毎日

```bash
kanban/bin/kb list                       # 今の todo / review / blocked
sandbox ls                               # 貸出中の VM が残っていないか
sandbox gh-app status                    # App と install が生きているか
```

貸出中の VM が run 無しで残っていたら（前日の `--keep` や中断）、`sandbox release <id>` で返します。

## トークン

### Claude Code のトークン（PJ ごと）

`claude setup-token` の長期トークンには有効期限があります。切れると agent step が認証エラーで失敗し、チケットは `blocked` になります。

```bash
claude setup-token
sandbox token set <pj>                   # 保存
sandbox reinject <id>                    # 貸出中の VM にも反映（巻き戻しなし）
sandbox reinject --all
```

期限はメモ（`workspace/docs/` など）に書いておく運用です。

### GitHub のトークン（自動）

GitHub App の installation token は 1 時間で切れます。launchd `com.aifactory.sandbox.gh-refresh` が 45 分ごとに貸出中の VM へ払い出し直し、runner も code step の前に払い出し直します。手動なら:

```bash
sandbox gh-app refresh                   # 貸出中の全 VM
sandbox gh-app token <pj>                # 払い出しの確認（トークンが表示される）
```

App の権限を足したとき（Actions: Read など）は、各 installation で承認が要ります。承認後は CLI が自動で要求します。

## プール

| やりたいこと | コマンド |
|---|---|
| 貸出状況 | `sandbox ls` |
| 空きなしで `take` が失敗 | `sandbox ls` で貸出中を確認 → 不要なら `release`。それでも足りなければ台数を増やす |
| 台数を増やす | `TPL_VMID=911x sandbox/proxmox/run.sh 40-pool.sh <pj> <台数>` → `50-firewall.sh`。`lvs pve/data` の `data%` を見る |
| 汚れた VM を作り直す | `qm destroy <vmid>` → `40-pool.sh`。`clean` が無い VM は `reset` できない |
| 貸出中の VM を触らない | `~/.config/sandbox/state.json` を読んで飛ばす（`50-firewall.sh` は `LENT=` で飛ばせる） |

## テンプレートの更新

| 層 | いつ | 手順 |
|---|---|---|
| PJ 層（依存の追加、seed の変更） | PJ の Gemfile / package.json が変わったとき | `32-pj-template.sh` で焼き直し → `40-pool.sh` でプール作り直し → `50-firewall.sh` |
| base 層（OS パッケージ、ツール、Claude Code の版） | 月 1 回 | `30-base-template.sh` → 全 PJ の 32 → 40 → 50。頻繁にやると PJ 層の作り直しが負担なので月 1 |

作り直す前に `sandbox ls` で貸出中が無いことを確かめます。

## 通信制限（firewall）

VM はインターネットと sb-gw の DNS にだけ出られます（ADR-0010）。LAN・Proxmox ホスト・隣の VM・tailnet には届きません。テンプレートやプールを作り直したら `50-firewall.sh` を再実行して、`clean` スナップショットに firewall 設定が含まれるようにします。

確認（汎用 VM を take して中から）:

```bash
sandbox take generic 999
sandbox ssh 999 'curl -sI https://github.com | head -1; ping -c1 -W1 <LAN 内のホストの IP> || echo "LAN 不可 (期待どおり)"'
sandbox release 999
```

## 障害と対処

| 症状 | 見るところ | 対処 |
|---|---|---|
| `task-xxx.sb.internal` が解けない | `dig sb-gw.sb.internal`、Tailscale の split DNS | split DNS が消えていないか。`ssh root@10.77.0.2 systemctl status dnsmasq` |
| 10.77.0.2 に ping 不可 | Tailscale 管理コンソールの route 承認、`pct exec 9000 -- journalctl -u tailscaled -n 20` | ACL の grant が無ければ足す。急ぎなら `SB_JUMP=<PVE_HOST と同じ値>` で Proxmox ホスト経由 |
| VM に ssh 不可 | `qm status 92NN`、`qm agent 92NN network-get-interfaces` | `qm start`。起動していれば `qm terminal` でシリアルから |
| `reset` が失敗 | `qm listsnapshot 92NN` に `clean` があるか | 無ければ破棄して `40-pool.sh` |
| VM から外に出られない | `iptables -t nat -S \| grep 10.77`、`pve-firewall status` | SDN 再適用 `pvesh set /cluster/sdn`。LAN・他 VM・tailnet 宛ては仕様で不可 |
| Mac から VM に届かない（firewall 有効化後） | `/etc/pve/firewall/<vmid>.fw`、`qm config <vmid> \| grep firewall` | `50-firewall.sh` を再実行 |
| Claude Code が認証エラー | VM 内 `env \| grep CLAUDE_CODE_OAUTH_TOKEN` | `claude setup-token` → `sandbox token set` → `reinject` |
| Proxmox ホストが落ちた | `ssh $PVE_HOST` 不可、`pvecm nodes`（クラスタなら別ノードから） | 電源を入れる（WoL / IPMI / 物理ボタン）。プールは onboot=0 なので手で `qm start` |

## 定期メンテ

- 月 1: base テンプレートの OS 更新
- トークンの期限が近づいたら `claude setup-token`
- `workspace/runs/` が増えたら、古い run を消すか別置きにする（記録としては `state.json` と `work/` があれば十分）
- `workspace/kanban/BOARD.md` の `done` が増えすぎたら、`kb list --all` で振り返ってから気にしない（DB は軽い）
