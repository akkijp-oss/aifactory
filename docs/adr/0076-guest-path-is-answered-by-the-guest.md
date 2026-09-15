# ADR 0076: ゲストの PATH はゲストが答える。ログイン環境を起点に版管理ツール（mise）へ訊き、制御系は道具を列挙しない

- 状態: Accepted
- 日付: 2026-09-15

## 状況

「ゲストはどうやって PATH を知るか」という設計判断が一度も下されていなかったため、3 つの pull backend の `command()` が**それぞれ違う答え**を実装していた。

| backend | PATH の決め方 |
|---|---|
| windows | `[Environment]::GetEnvironmentVariable('PATH','Machine')` — **ゲストのマシン環境変数に訊く** |
| macos | 固定 4 件の列挙 `/opt/homebrew/opt/coreutils/libexec/gnubin:/opt/homebrew/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH` |
| linux | 固定 5 件の列挙 `/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin:$HOME/.cargo/bin`（`$PATH` を継がない） |

列挙方式は「新しい版管理ツールを入れるたびに backend を直す」設計で、**直し忘れた 1 か所が「その道具だけ存在しないゲスト」になる**。2026-09-15 にこれを踏んだ:

1. macOS ゲストで `go` が `command not found`。実体は `~/.local/share/mise/shims/go`。macos / linux のどちらの列挙にも `mise` の shims が無い。
2. `npm` / `node` は `/opt/homebrew/bin`、`cargo` は `$HOME/.cargo/bin` が列挙にあるので**見つかる**。**半分だけ動くので原因が分かりにくい。**
3. 実装役は毎 run 自力で `find / -name go` まで走らせて mise を見つけ、回避していた（1 run あたり約 40 秒 + 数ターン。3 run とも同じ探索を繰り返した）。
4. 最悪の形として、`go vet ./...` が `command not found` のまま **rc 0 で終わる**呼び出し方（背景実行 + `| tail`）があり、「ベースライン緑」と誤読しうる。

着手時の実測で、起票時の見立てと違う事実が 3 つ出た。**この ADR はその実測を正とする。**

- macOS の worker は**既に**ログインシェルを通している（`tart exec <guest> /bin/bash -lc '<command>'`。`workers/cmd/aifactory-worker/main.go`）。
- それでも `go` が消えるのは、上流イメージ（cirruslabs の `base.pkr.hcl`）が `brew install mise` するだけで、**`~/.zprofile` に mise の有効化（`mise activate` / shims）を書いていない**ため。`.zprofile` が足すのは brew shellenv・rbenv・node@24・PNPM_HOME だけ。
- `examples/projects/*/provision.sh:8` の `export PATH="...mise/shims:$PATH"` は**スクリプト自身のプロセスにしか効かず**、ゲストのどこにも残らない。「provision が入れた PATH がログインシェル経由で効く」は成り立っていない。
- linux-pull の worker は `systemd-run ... --setenv=PATH=/usr/local/bin:/usr/bin:/bin -- /bin/bash --noprofile --norc -c`（`platform_linux.go`）で走らせる。**ユーザーの rc も `/etc/profile` も読まれない**ので、列挙にある `$HOME/...` は空振りし、管理者が `/etc/profile.d/*.sh` に置いた PATH も効かない（同型の穴。実害の報告はまだ無い）。

つまり **「`bash -lc` に変えるだけ」では `go` は直らない**。

## 決定

1. **ゲストの PATH はゲスト自身に答えさせる。制御系は道具を列挙しない。** windows の「マシンに訊く」形が既に正解なので、**windows は変えない**。macos / linux を同じ規則に揃える。
2. **前置きは 1 か所で組む。** `macos.py` の `pull_backend.guest_path_prelude()` が正本で、linux backend はそれを**継承**し、保険の固定列挙（`PATH_FALLBACK`）だけを差し替える。3 ファイルに散った列挙をそのままにしない。
3. **段の順序に意味がある。** 次の 3 段を `;` で連結する（`&&` で終わらせない・バックグラウンド化しない。前置きがゲストの命令の rc を作り替えないため）。
   1. `shopt -q login_shell || { test -r /etc/profile && . /etc/profile; }` — **ログイン環境を起点にする。** macOS は worker が既に `bash -lc` なのでここは何もしない（**macOS で再 source しないこと**。`path_helper` が PATH を並べ替えて `/opt/homebrew/bin` が後ろへ下がる）。linux-pull では `/etc/profile` → `/etc/profile.d/*.sh` を読み、管理者がゲスト側に置いた PATH を通す。
   2. `export PATH=<PATH_FALLBACK>:$PATH` — **保険の固定列挙。**「ゲストが PATH を答えられない」ときだけ効く。並びは従来どおり（macOS の gnubin を先頭に残す）。Ubuntu の `/etc/profile` は PATH を上書きしてから profile.d を読むので、**この export は 1 の後**でなければ消える。
   3. `if command -v mise >/dev/null 2>&1; then eval "$(mise activate bash --shims 2>/dev/null)"; fi` — **版管理ツール自身に訊く。** `if` 文にするのは、末尾が偽の `&&` だと前置き全体の rc を汚すため。
4. **固定列挙は「新しい道具を通すため」に増やさない。** 通したい道具はゲスト側（`/etc/profile.d/*.sh` か版管理ツール）に入れる。`PATH_FALLBACK` はログイン環境が使えないゲストのための後方互換であって、道具の目録ではない。
5. **`command not found` は非ゼロのままゲストから制御系へ届く**ことを単体で固定する。前置きは rc を握らないので `sb()` は `RuntimeError`、`run_remote()` は 127 を返す。PATH を直すだけでは、次に別の道具が消えたとき同じ誤読が起きる。

### なぜ「ログインシェルだけ」にしないのか

起票時の設計案は「`zsh -lc` / `bash -lc` を通せば列挙は要らなくなる」だった。**これは今回の `go` を直さない。** 上記の実測どおり macOS は既にログインシェルで、上流イメージの `.zprofile` に mise の有効化が無いからである。ログイン環境は**起点**として使い、そこに載っていない版管理ツールの shims は**ツール自身に訊く**（決定 3 の 3 段）。

### ADR-0071 との関係

ADR-0071（Go は PJ テンプレートで焼き `/usr/local/bin/go` から張る）は「**ゲスト側で道具を既定の場所に置く**」、本 ADR は「**制御系側は列挙せず、ゲストの答えを使う**」。矛盾ではなく同じ方向（**正本をゲスト側の 1 か所に置く**）を別の層から見ている。0071 が「agent が開く素の bash はログインシェルとは限らない」と書いたのは agent が VM 内で直に叩く shell の話で、本 ADR は制御系が guest-exec に渡す前置きの話。決定 3 の 1 により、`/etc/profile.d/go.sh` が **linux-pull でも効くようになる**。

ADR-0075（base 確認は自分のログに書く）と構図が同じ: **同じ仕組みを 3 か所に散らして書くと、直し忘れた 1 か所が事故になる。**

## 結果

- macOS / Linux のゲストで、mise が管理する道具（`go` 等）が guest-exec 経由で解決する。実装役が毎 run 探索に焼いていた時間とトークンが要らなくなる。
- 版管理ツールを新しく入れても backend を直さなくてよい。`provision.sh` が入れた PATH と guest-exec が見る PATH の食い違いは、ゲスト側（profile.d / mise）に置けば構造的に消える。
- トレードオフ:
  - `mise activate` の呼び出し分（数十 ms）が毎コマンドに乗る。
  - mise 以外の版管理（asdf・rbenv 等）は対象外。通したいならゲストのログイン環境か `/etc/profile.d/*.sh` で足す（列挙を増やすのではなく）。
  - linux-pull は `/etc/profile` を読むので profile.d の内容に依存する。対話前提のスクリプトが警告を出しても `;` 連結なので rc には影響しない。
  - `mise activate bash --shims` の出力の形は mise の版に依存する（2026.9.1 で `export PATH="$HOME/.local/share/mise/shims:$PATH"` を確認）。単体テストは偽 mise で見るので、実物の版変更は検出できない。実機で通らなければ `mise activate --help` で `--shims` の有無を確かめる。
- `workers/` は変えない。linux worker の `--noprofile --norc` は worker 側の設計判断として残し、**前置き側で補う**。
