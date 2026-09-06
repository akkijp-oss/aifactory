# 0004 Mac からの到達はゲートウェイ LXC の Tailscale subnet router

日付: 2026-09-05 / 状態: 採用

## 状況
Mac と Proxmox は Tailscale で同一 tailnet。sandbox VM には専用 IP 空間 10.77.0.0/16 を割り当てる（メンテナの判断「IP 空間を分けたい」）。Mac からそこへ届く方法として、(a) 各 VM に Tailscale を入れる、(b) ゲートウェイ1台だけ tailnet に入れて subnet router にする、があった。

## 決定
**(b)**。LXC `sb-gw` を1台置き、`10.77.0.0/16` を advertise する。同じ LXC に dnsmasq を載せ `*.sb.internal` を返し、Tailscale の split DNS で Mac から名前が引けるようにする。VM には Tailscale を入れない。

## 理由
- (a) はスナップショット巻き戻し（0003）と相性が悪い。VM 内のノード鍵が巻き戻るたびに tailnet 上で重複・再認証が起きる
- (b) は VM が素のままで、巻き戻しても何も壊れない
- SDN の SNAT で外向きはホストが NAT するので、ゲートウェイは NAT 不要。役割が subnet router と DNS だけになり単純

## 結果
- Tailscale の認証と route 承認、split DNS 設定は人間の操作（`BUILD.md` Step 2b）
- `sb-gw` が落ちると Mac から届かなくなる。onboot=1 で自動起動。VM 同士やホストからの到達には影響しない
- VM のホスト鍵検証は無効化している（内部ネット限定の割り切り。`sandbox/README.md` 秘密情報の節）
