# ADR 0068: 止まったゲストは `guest-start` で起動し直し、`--resume` がそれを自動で呼ぶ（起動できない回は作り直さず止まる）

- 状態: Accepted
- 日付: 2026-09-13

## 状況

ADR-0066 の事故（#446）で、ゲストが止まった run には **続きを回す手段が無かった**。

- `kb run <id> --resume` は `resume_guest()`（`workflow/lib/macos.py`）がゲストの中を `guest-exec` で覗きに行く。
  ゲストが止まっていれば `tart exec` がそのまま失敗し、"prepared" にも "provisionable" にもならないので、
  必ず `Mac setup is incomplete or the guest is stopped` で止まる。
- worker 側に「既存のゲストを起動するだけ」の operation が無い。`guest-prepare` は
  「lease ファイルがある / 同名ゲストがある」だけで uncertain に落として拒否する（作りかけ・他 lease の
  ゲストを新規 run が乗っ取らないための防御。ADR 以前からの `TestPrepareRefusesPreexistingGuest`）。
  `guest-release` は必ず `tart delete` まで進む。「止めるだけ・起動するだけ」が無い。
- 結果、実際の復旧は `guest-release` → `release-lease` → 新規 run で、実装をやり直す約 45 分が捨てられた
  （asura #408 の実測）。ADR-0067 の保全が入って実装の消失は減ったが、**止まったゲストから続ける**口は別に要る。

`guest-prepare` の拒否と、この復旧は **区別できる**。前者は「lease ファイルが無いのにゲストがある」、
後者は「lease ファイルの中身がこの run の lease と一致していて、そのゲストが止まっている」であり、
lease ファイルの有無と一致で判別がつく。

## 決定

1. **起動は worker の新しい operation `guest-start` でやる**。停止したゲストを `tart run` で起動し直し、
   `tart exec` が通るまで待って DNS/IPv6 を設定するところまで。`guest-prepare` と同じ手順・同じフラグを使うため、
   起動と待ち受けは `bootGuest()` / `awaitGuest()` に切り出して両方から呼ぶ。
   `guest-start` は **`tart clone` / `tart set` / `tart delete` / lease ファイルの書き換えをどれもしない**。
   ゲストの中身を制御系から直接いじる経路は無いので、起動そのものは必ず worker 側の tart 呼び出しになる。
   制御系だけで閉じる案（選択肢 2 単独）は成立しない。
2. **起動し直せない状態は、黙って作り直さずに止まる**。ゲストが `tart list` に無ければ `failed`（exit 1）と
   `guest missing; nothing changed; lease=…` の 1 行で返す。ここは tart を 1 つも呼んでいないことが
   worker 側で確定しているので、人の `resolve` を要る uncertain ではなく failed にする。
   `tart run` を発行した後の失敗（待ち受け切れ・DNS 設定失敗）は `guest-prepare` と同じく uncertain。
   どちらでも制御系は RuntimeError で止まり、**新規ゲストは作らない**。
3. **`--resume` は、能力を広告している worker のときだけ `guest-start` を自動で呼ぶ**。
   `info()` に `guest_start` を足し（Mac の lifecycle worker のみ。`version` は 0.4.0）、`resume_guest()` は
   lease 一致を確かめた直後、ゲストの中を覗く前に 1 回だけ投げる。広告の無い古いバイナリには投げない。
   `pull.py` の `submit` も `guest_start` の無い worker への `guest-start` を 409 で拒む。
   広告とサーバ側拒否の **二重のガード**にするのは、知らない kind が worker に届くと落ち穂拾いの uncertain に
   なって worker ごと塞がるため。どちらか一方では足りない。
4. **失敗しても lease もゲストも消さない**。`guest-start` が失敗したときに `guest-release` を自動で呼ぶ・
   `tart delete` する・lease ファイルを消す、はどれもしない。人が検査するために残したものを、
   復旧の口が壊すことになる。`MacRun.main()` の except（lease を持ったまま人に返す）と同じ方針。
5. 既に動いているゲストへの `guest-start` は `tart run` を重ねず、待ち受けだけして成功で返す（冪等）。
   人が手で起動した後・resolve が「動いたまま」だった回に、二重起動で壊さないため。

## 理由

- 選択肢 1（`guest-start`）と 2（`--resume` が起動する）は排他ではない。起動は必ず worker の仕事なので
  1 が土台で、2 が上に乗る。片方だけでは完了条件「停止したゲストを持つ run を `--resume` で続けられる」を満たさない。
- 「起動し直せない状態は明示的に失敗」は `guest-prepare` の「作りかけを拒否する」と同じ思想である。
  黙って新規作成に倒すと、人が検査しようと残したゲストが消え、事故の原因が読めなくなる。
- ADR-0047（`--resume` の開始工程は `state.json` の `history` から決める）はそのまま。`guest-start` は
  ゲストを起動可能にするだけで、どの工程から再開するかには触れない。
- ADR-0067 の `preserveWork`（止める前の wip push）とは絡めない。`guest-start` は「止める」操作ではない。
- ADR-0066 の決定 7 は worker 側の残りを別票に切った。ADR-0067 が「止まる前に実装を残す」側、
  本 ADR が「止まった後に続ける」側で、2 枚で #446 の残りを閉じる。

## 帰結

- 復旧の手順が変わる。`docs/macos-worker.md` の「uncertain からの復旧」で、
  「ゲストが落ちている → lease を片づけて新しい run を投げ直す」は
  「**止まっているだけなら lease を解放せず `kb run <id> --resume`**。一覧に無い・起動できないときだけ片づける」になる。
  `aifactory-drive` スキルの `aif-mac-recover.sh` が前提にしていた「`--release` で削除 → 新規 run」も、
  「まず起動し直す」に置き換えられる（スクリプトの実体はこのリポジトリには無い。正本はこの docs 側の手順）。
- 手で診断したいときは `control submit <worker> guest-start --lease auto --wait 300` が使える。
- worker のバイナリを更新しないと `--resume` の挙動は変わらない。古いままだと従来のエラーで止まるが、
  そのエラー文に「この worker は guest-start を広告していない」が付くので、更新すべきことが読める。
- **限界**: 実機（tart）で「停止したゲストが `tart run` でそのまま起動できる」ことは未実測である。
  ユニットテストは `w.command` のモックで固定してあり、実機手順は run の report.md に残した。
  ディスクが壊れているなど「ゲストは在るが起動できない」回は `tart run` 後の待ち受け切れ = uncertain に落ちる。
  これは人の `resolve` が要る状態だが、ゲストが残っているので検査はできる。

## 代替案

- **`guest-prepare` に「既存ゲストを引き取る」分岐を足す**: 既存ゲスト拒否は作りかけ混入への防御そのもので、
  同じ operation に「拒否する」と「引き取る」を同居させると、防御が条件分岐に埋もれて読めなくなる。採らない。
- **`guest-release` に「削除しない」選択肢を足す**: 削除する操作に削除しない道を作るより、
  起動という別の操作を足すほうが、名前と危険度が一致する。採らない。
- **ゲストが無いときに黙って `guest-prepare` に倒す**: 完了条件に真っ向から反する。
  人が残した検査対象を消したうえ、45 分の作り直しを黙って始めることになる。採らない。
