# ADR 0045: VM に渡す Claude の鍵は鍵プールを正本にし、`sandbox token set <pj> claude` は非推奨にする

- 状態: Accepted（ADR-0006 の「PJ ごとの env ファイル」を鍵プール（ADR-0044）で置き換える。ADR-0006 の仕組みは互換のため残す）
- 日付: 2026-09-10
- メンテナの判断（2026-09-10）: PJ ごとに `sandbox token set` した鍵は鍵プールから選ぶ。`token set` は非推奨にして「代わりにこちら」と案内する

## 状況

ADR-0044 で鍵プール（`~/.config/sandbox/keys.json`。console の「鍵」画面と `sandbox keys` が読み書き）を入れたが、
PJ ごとの env ファイル（`sandbox token set <pj>`）も並存していて、鍵の置き場が 2 つあった。実機（main テナント）では
global と 12 の PJ ファイル全部に同じ 1 本が入っていて、プールには別の 2 本があり、どれがどの run に使われるかを人が
追えなかった。鍵は PJ の性質ではなく契約の性質（ADR-0044）で、置き場は 1 つでよい。

## 決定

1. **VM に渡す Claude の鍵の正本は鍵プール。** 入口は console の「鍵」画面と `sandbox keys add|set|rm|token` の 2 つで、
   どちらも同じ `keys.json` を読み書きする。
2. **`sandbox token set|clear <pj|global> claude[:<系統>]` は非推奨。** 互換のため動かすが、stderr に「非推奨。`sandbox keys add`
   か console の「鍵」画面を使う」と出す。`gh`（GitHub App が無いときのフォールバック）はそのまま。
3. **env ファイルの鍵は「プールに合う鍵が無いとき」だけの保険。** ADR-0044 の規則のまま（プールが用途をどちらも
   持っていれば env の鍵は使われない）。実機では PJ ファイルから Claude の鍵を全部消し、global に 1 本だけ残した
   （値はプールの `main-shared` と同じ）。
4. **`sandbox token rotate claude` は残す。** 対象は env と `ctl.env`（intake が使う鍵）で、プールは触らない。
   プールの鍵の差し替えは `sandbox keys token <名前>` か「鍵」画面。
5. **console の sandbox 画面の「トークン」列は「Claude の鍵」列にし、出どころを言う。** 鍵プール / 鍵プール（片方の用途だけ）/
   PJ 別の設定ファイル（非推奨）/ 全体の設定ファイル / 未設定。値は見ない（有無だけ）。
6. **言い方を console と CLI で揃える。** 「Fable に使う」「Opus・Sonnet・Haiku に使う」「有効」「登録日」「最後に使った日時」。
   CLI のオプション名（`--fable` / `--other`）は変えない。

## 対象外（別票）

- `glue/bin/intake` は `ctl.env` の鍵のまま（ADR-0044 の対象外を引き継ぐ）。プールから選ぶようにするのは別票。
- pull backend（macOS / Windows / Linux の worker）は `provision.sh` の経路のまま。

## 結果

- 鍵の追加・停止・入れ替えは「鍵」画面 1 か所で済み、どの run がどの鍵を使ったかは貸出行と run のログ（`key=… (pool: <名前>)`）で追える。
- `token set <pj>` を使う古い手順書（website / README / OPERATIONS）は、プールを先に書き、`token set` は非推奨と注記する。
