# ADR 0057: ゲストの表示解像度は PJ 定義 > worker 設定 > 基準 VM のまま、の順で決める（scale は未対応）

- 状態: Accepted
- 日付: 2026-09-07

## 状況

Mac worker の専用ゲスト（Tart VM）は基準 VM に焼き込まれた 1024×768 で起動する。`guest-prepare` は
`tart set <guest> --cpu 4 --memory 8192` しか呼んでおらず、解像度を渡す口が無い。

- asura のように「1400 / 900 / 600px 幅で並べて目視比較する」規約を持つ PJ では、1400px 幅の確認が毎回「未確認」で終わる。
- 画面を広げるだけでは足りない。`workers/computer/macos.swift` は screenshot もクリック座標も幅を
  `min(1024, 実解像度)` に縮小しており、screenshot は 1024 幅のまま返る。
- worker は PJ を跨いで共有する 1 台なので（`docs/macos-worker.md`）、worker 設定だけでは PJ ごとに変えられない。
  PJ ごとに変えるには `guest-prepare` の payload に乗せるしかないが、制御系 `workers/lib/pull.py` は
  lifecycle 操作の payload を `{"lease"}` の完全一致でしか受け付けていない。
- ゲストの割当（4 CPU・8 GiB）は解像度と無関係な別の引数で、これは変えない前提が既に文書化されている。

## 決定

1. **`project.yml` に `display: {width, height}` を足し、worker 設定 `config.json` にも同じ形の `display` を置く。**
   優先順位は **PJ 定義 > worker 設定 > 指定なし**。指定なしのときは `--display` を 1 度も呼ばず、基準 VM の 1024×768 のまま動く。
2. **`tart set <guest> --display <W>x<H>` は `--cpu/--memory` とは別のコマンドとして、`tart run` の前**（＝クローンが停止中）に実行する。
   cpu/memory の引数は 1 文字も変えない。
3. **値域は幅 800〜2560・高さ 600〜2560 の整数で、`width` と `height` は両方必須。** 同じ範囲を 3 か所で検査する
   （`project.schema.json` / 制御系 `pull.py` の payload 許可リスト / worker の起動時検査）。範囲外の worker 設定では worker を起動しない。
4. **`pull.py` の lifecycle 許可リストを緩めるのは `guest-prepare` の `width`/`height` だけ**にする。`guest-release` は `{"lease"}` のまま、
   `guest-exec` の検査は触らない。
5. **`macos.swift` の上限を 2560 に上げる。** screenshot とクリック座標変換が同じ名前付き定数を使うようにして、両方を必ず一緒に動かす。
   `windows.ps1` / `linux.py` の 1024 上限は変えない。
6. **`display.scale` は受け付けない。** schema で拒否し、未対応であることを docs と本 ADR に書く。

## 理由

- PJ 定義を優先にするのは、worker が複数 PJ の共有物で、解像度は「その PJ の確認手順」に属する事実だから。worker 設定は
  その worker を使う全 PJ の既定として残す（1 PJ しか使わない worker で PJ 定義を書き回さずに済む）。
- 「指定が無ければ 1 度も呼ばない」は、`tart set --display` の構文をリポジトリ内でも VM 内でも一次情報で確認できないため。
  構文が違っても、既存 PJ の prepare は今までと同じコマンド列で動く。壊れるのは新しく `display` を書いた PJ だけに閉じる。
- swift の上限を Mac だけ上げるのは、チケットの対象が Mac ゲストで、Windows / Linux のワーカーは物理画面が 1024 を超えていると
  screenshot の大きさが黙って変わってしまうから。座標変換も同じ定数を読むので、片方だけ上げるとクリック位置がずれる。
- 上限 2560 は schema の幅上限と同じ値にした。schema を通った解像度なら必ず等倍で返る、という関係が成り立つ。
- `scale` を落とすのは、`tart set` の対応オプションが確認できておらず、推測で引数を組むと prepare が失敗するから。
  受け付けて無視するより、検証エラーで落として「まだ無い」と分かる方が運用で迷わない。

## 結果（トレードオフ）

- 制御系の payload 許可リストが完全一致でなくなる。追加は `guest-prepare` の `width`/`height` の 2 キーだけで、
  値域も型も検査するが、lifecycle の payload に「lease 以外が乗ることがある」形にはなった。
- `desktop-native`（swift のバイナリ）は基準 VM に焼き込む運用なので、コードを直しても既存の基準 VM には反映されない。
  入れ替えて基準 VM を作り直すまで、画面を広げても screenshot は 1024 幅に縮小されたまま返る。この運用上のラグを docs に明記した。
- `tart set --display` の実行と screenshot の実サイズは VM 内の自動テストでは確かめられない（tart も Mac も無い）。
  自動で確かめるのは「引数の組み立て・順序・値域・payload の受け渡し」まで。実機確認は Mac 運用者の手作業になる。
- 解像度を上げると PNG が大きくなるが、工程ログには画像の base64 を `[image N bytes]` に置き換えて記録するので（チケット 280）、
  1 操作 16 MiB のログ上限には解像度に関係なく当たりにくい。追加の対応は入れず、docs に関係を書くだけにした。
- `scale` は未対応のまま。Retina 相当の見た目が要る確認は、`tart set` のオプションを実機で確認してから別チケットで扱う。
