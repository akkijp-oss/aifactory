# 0010 sandbox VM の通信は「インターネット」と「sb-gw の DNS」だけに絞り、LAN・他 VM・tailnet へは出さない

日付: 2026-09-06 / 状態: 採用

## 状況
メンテナが「sandbox から Mac や他の VM にアクセスできるようだが塞ぐべきか」と指摘（2026-09-06）。実測したところ、VM から次に届いた:
- Proxmox ホストの SSH（LAN 側と sbnet 側 10.77.0.1）、sb-gw の LAN 側、LAN 上の機器全般（ホストが SNAT で LAN へ出しているため）
- 隣の sandbox VM の 22 と 3000（同じ L2）
- 経路を 1 行足せば sb-gw 経由で tailnet（Mac の tailnet アドレスに ping が通る）

VM の中の agent は `--dangerously-skip-permissions` で任意のコマンドを実行し、リポジトリや Web から読んだ内容に従って動く。「VM の中で何をしても外に影響しない」が sandbox の前提（README 目的 1）であり、これが崩れていた。

## 決定
1. VM に必要な通信は「外向き: インターネット + sb-gw の DNS（53）」「内向き: Mac からの ssh / アプリ（sb-gw が SNAT して中継 = 送信元 10.77.0.2）と、Proxmox ホスト（10.77.0.1）からの起動確認」だけと定義する
2. **Proxmox firewall を VM の NIC 単位で使う**（`net0 ... firewall=1` + `/etc/pve/firewall/<vmid>.fw` に `GROUP sandbox`）。datacenter レベルは `enable: 1` にするが `policy_in/out: ACCEPT` にして **ホスト側の挙動は変えない**（締め出し防止）
3. セキュリティグループ `sandbox`（`cluster.fw`）: IN は 10.77.0.2 / 10.77.0.1 / 100.64.0.0/10 からの 22, 3000, 5174 と ICMP のみ。OUT は 10.77.0.2:53 を許可 → 10.77.0.0/16、192.168.0.0/16、10.0.0.0/8、172.16.0.0/12、100.64.0.0/10、169.254.0.0/16 宛てを DROP → 残りを許可
4. sb-gw では `FORWARD -i eth1 -o tailscale0 DROP` と `-i eth1 -o eth0 DROP` を入れ、VM 発の転送を落とす（systemd unit `sandbox-fw` で永続化）
5. プールの `clean` スナップショットは firewall=1 を含む config で取り直す（rollback で config が戻るため）。以後の作成スクリプト（30 / 32 / 40）は firewall 設定込みで作る

## 理由
- VM 同士の遮断は L2 で同じブリッジに居るためホストの iptables では効かず、NIC 単位（Proxmox firewall の fwbr）が必要
- 許可リスト方式（インターネットと DNS だけ）にすると、LAN に新しい機器が増えても穴が開かない
- ホスト側のポリシーを ACCEPT のままにしておけば、クラスタ全体で firewall を有効化しても既存の運用（Mac から各ノードへの SSH、同じクラスタの他の VM）に影響しない

## 結果（トレードオフ）
- VM から Proxmox ホストや他 VM に意図して届かせたい場合（例: 共有サービス）は、group に例外行を足す ADR が要る
- テンプレート焼き込み（provision.sh）はインターネットだけで完結するので影響なし。`sandbox/proxmox/run.sh` 経由の操作はホスト側なので影響なし
- 検証は `sandbox/proxmox/50-firewall.sh` 適用後に汎用 VM からの到達テスト（LAN / ホスト / 隣 VM / tailnet = 不可、GitHub / DNS = 可、Mac → VM = 可）で行う
