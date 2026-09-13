# ADR 0071: sandbox の Go は PJ テンプレートに焼き、版は `workers/go.mod` から読む。満たせない run は prepare で止める

- 状態: Accepted
- 日付: 2026-09-13

## 状況

`workers/`（Go）を触る run で、実装役が**毎回 Go 自体を調達している**。

- 2026-09-13 の #477: `which go` が rc=127 → 公式 tarball を `/home/dev/go-sdk` に展開して `go test -race` を実行。
- 同日 #478: 同じことを `/tmp/go` で再実行。plan 段でも「VM に Go が無く apt は 1.22 で `go.mod` の要求に足りない」の調査に時間を使い、
  「無理なら CI を根拠に report.md へ明記」という代替案の検討まで発生した。レビュアーも人への申し送りに書いている。

VM は run の終わりにテンプレートへ戻るので、この調達は次の run に残らず、まるごと繰り返される。さらに #477 の本文は
「Go が入った環境で回すこと」と**入っている前提**で書かれていた。前提が現地で成り立たず、実装役が自力で埋めている
（埋められなければ、検証していない Go の変更がそのまま PR になりうる）。

`apt` の `golang-go` は Ubuntu 24.04 で 1.22 で、`workers/go.mod` の要求に届かない。回避のやり方は run ごとに違っていた。

## 決定

1. **Go は PJ テンプレート（`examples/projects/aifactory/provision.sh`）で焼く。** 公式 tarball を `/usr/local/go` に展開し、
   `/usr/local/bin/go` から張る（login shell でなくても `go` が見つかる）。モジュールキャッシュ（`go mod download`）も一緒に焼く。
2. **版はどこにも書かず `workers/go.mod` の `go` 行（`toolchain` 行があればそちら）から読む。** 読み取りと導入と確認は
   `bin/go-toolchain.sh`（`required` / `url` / `check` / `install` / `ensure`）の 1 か所に置く。CI も
   `actions/setup-go` の `go-version-file: workers/go.mod` で同じ行を見ている。
3. **満たしているかは貸出直後の `prepare`（ADR-0038）で機械が確かめる。** テンプレートは焼いた時点で固定、`go.mod` は
   base で進むので、ずれは必ず起きる。足りなければ `prepare` が同じ手順で入れ直し、**それでも満たせなければ非 0 で終わる**
   （runner は agent を 1 つも起こさず `failure: prepare` で人へ返す）。古い Go で `go test` を通さない。
4. **基準イメージ（base テンプレート）には焼かない。** Go を使うのは `workers/` を持つこの PJ だけで、base は全 PJ 共通の層。
   イメージの焼き直しはプールの作り直しを伴い、オーナー領分（チケット 488 の注意書き）。
5. **ゲートには足さない。** 環境の不足は実装役ではなく人が直すもので、ゲートに入れると `implement` へ戻る（ADR-0038 の理由と同じ）。
   Go のテスト自体をゲートに入れるかは別の判断として残す（今は CI の `worker` ジョブが見ている）。

## 理由

- **なぜ版をハードコードしないのか。** `go.mod` を上げたときに黙ってずれるため。ずれた状態は「古い Go でテストが通ってしまう」
  という一番気づきにくい形で出る。正本を 1 つにすれば、ずれは `prepare` の 1 行として必ず見える。
- **なぜ `prepare` で入れ直すのに、失敗は止めるのか。** テンプレートを焼き直すまでの間、run を全部止めるのは高すぎる
  （この PJ は自分自身なので、文書だけの run も止まる）。一方、入れ直せないまま進めると、実装役が「Go が無い」の調査に
  attempt を焼く。入れ直せるなら黙って進めず 1 行警告して進み、入れ直せないなら人に返す、が両方の損を小さくする。
- **なぜ `/usr/local/bin` から張るのか。** `sandbox ssh` は `bash -lc` なので `/etc/profile.d/go.sh` で足りるはずだが、
  agent が開く素の `bash` は login shell とは限らない。既定 PATH にある場所から張れば、どちらでも `go` が見つかる。

## 結果（トレードオフ）

- テンプレートの焼き込みが数十秒延びる（tarball の取得と展開、`go mod download`）。run 側は `check` の数十 ms だけ。
- `go.mod` を上げた直後の run は、テンプレートを焼き直すまで `prepare` で毎回 Go を取り直す（今と同じ待ち時間だが、
  実装役の時間ではなく準備の時間になり、`code-prepare.log` に「焼き直せ」と残る）。
- tarball は https の公式配布元から取るだけで、チェックサムは照合していない（base の Chrome や mise と同じ流儀）。
  照合するなら `https://go.dev/dl/?mode=json` の sha256 を見る余地がある。
- この PJ の定義に `prepare` が増えたので、**配布された PJ 定義に `prepare.sh` が無い制御系では `run` が起動前に止まる**
  （`project.yml` と一緒に配られるので、両方古いか両方新しいかのどちらかになる。ずれは gates の `INFO pj-drift` に出る）。
