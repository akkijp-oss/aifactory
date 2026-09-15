# ADR 0075: base 確認は自分のログ（`~/gates/<名前>.base.log`）に書く。run が残す証跡は後続の処理で壊さない

- 状態: Accepted
- 日付: 2026-09-15

## 状況

ADR-0038 決定 3 で、ゲートが赤いとき runner は**その赤いゲートだけ**を `origin/<base>` でも回し直し、「base でも赤いのか」を機械で判定するようにした。この再実行は**診断のため**に走る。

ところが PJ の `gates.sh` の `gate()` は出力先が `~/gates/<名前>.log` 固定で、base 確認は同じゲート名で走る。つまり**診断のための再実行が、診断対象（本実行の赤いログ）を上書きして消していた**。ADR-0040 で足した `work/gates/<名前>.log` への転記は base 確認の前に取っているので抜粋は残るが、VM の中の全文は base の結果に置き換わる。

2026-09-14、kumitate #521（run `2026-09-14-kumitate-521`）でこれを踏んだ:

1. `code-gates-3.log` が `FAIL test` を出した。
2. `~/gates/test.log` を読むと全 5 パッケージ緑・失敗行ゼロ（`apps/web` 837 files / 12144 tests passed）。判定行と中身が矛盾する。
3. 管理役は OOM・timeout ラッパ・ログ上書きを順に検討し、**mtime が step 終了の 4 秒前だったことを根拠に上書き説を自分で否定した**（誤り。base 確認は step の終わり際に走るので mtime は新しい）。
4. 実際は上書きだった。裏付けは「2 周目の `code-gates-5.log` には `BASE-CHECK` 行が無い」こと（BASE-CHECK は赤のときだけ走る）。
5. 真因は新規テストの `mock.calls[0][0]` に非 null アサーションが無いだけで、実装役は 14 秒で特定した。**証拠さえ残っていれば即座に分かった。**

この誤診に gates 1 周（約 18 分）と管理役の調査数十分を溶かした。同じ構図を #523 でも踏んだが、そのときは「BASE-CHECK 行がある＝詳細ログは上書き済み」と読めたので即解決した。**知っていれば避けられるが、知らなければ必ず一度は溶かす**類の罠で、知識で回避する（毎回読み手が気づく）ことを前提にしてはいけない。

## 決定

1. **run が残す証跡は、後続の処理で壊さない。** これは「あったら良い機能」ではなく、run 記録が証拠として意味を持つための**不変条件**として扱う。診断のために足した処理が診断対象を書き換えるのは、機能追加ではなく退行。
2. **本実行と base 確認でゲートログの名前空間を分ける。** 本実行は `~/gates/<名前>.log`、base 確認は `~/gates/<名前>.base.log`。
3. **分け方は PJ の `gates.sh` の契約に env `GATES_LOG_SUFFIX` を足して行う**（ADR-0038 決定 6 の「引数があればその名前だけ」と同じ枠）。`gate()` は出力先を `$HOME/gates/$name${GATES_LOG_SUFFIX:-}.log` と書く。未設定なら従来どおり `<名前>.log` なので、本実行の挙動は変わらない。
4. **`kit/steps/gates.sh` は base 確認の 1 呼び出しにだけ `GATES_LOG_SUFFIX=.base` を渡す**（export しない）。本実行の `bash ~/gates.sh` には渡さない。渡すと全部 `.base.log` になり、事故が逆向きに再発する。
5. **`code-gates-N.log` / `gates.txt` の案内は正しいパスを指す。** `BASE-CHECK origin/<base> <sha> (logs: ~/gates/<name>.base.log)` とし、base 側ログ末尾の見出しも `--- <名前>.base.log on base (tail 30)` にする。
6. **契約を守っていない PJ には 1 行言う。** base 確認の後に `~/gates/<名前>.base.log` が無ければ `=== base check:` の中に `BASE-CHECK-LOG-MISSING <名前> (...)` を出す。**FAIL / INFO にはしない**（ADR-0038 の判定は不変）。位置は `=== base check:` より後なので、runner の `^FAIL (\S+)` や console の判定行の読み取りには入らない。
7. **「base でも赤いか」の判定そのものは変えない。** 読むのは `base-check.sh` の標準出力（`PASS` / `FAIL` / `BASE-CHECK-SKIP` / `BASE-RESTORE-FAILED`）のままで、ログの置き場だけを変える。

## 理由

- **なぜ PJ 側に env を見せるのか（kit 側だけで済ませないのか）。** ログを書くのは PJ の `gates.sh` で、kit はそれを呼ぶだけ。kit 側で事後に退避（`mv`）すると、base のゲートが走っている最中は本実行のログが既に潰れており、途中で落ちた run では救えない。書き出し先そのものを分けるのが唯一の確実な方法。
- **なぜ `${GATES_LOG_SUFFIX:-}` と既定なしにするのか。** PJ の `gates.sh` は `set -u` で走る。未設定でも落ちないこと、かつ**本実行が従来と 1 バイトも変わらない名前**になることが、既存の run 記録・文書・人の手順を壊さない条件。
- **なぜ `BASE-CHECK-LOG-MISSING` を FAIL にしないのか。** 追跡外の私有 PJ 定義（`workspace/projects/`）はこの repo から直せず、契約を足すのは人の 1 行の仕事。FAIL にすると、その PJ の全 run が implement へ戻る（ADR-0042 §4 と同じ理由）。黙って元の事故に戻らないための可視化であって、品質の判定ではない。
- **なぜ ADR-0038 / ADR-0040 を書き換えないのか。** どちらも決定そのものは正しく、崩れていたのは「ゲートログは 1 つの名前空間で足りる」という暗黙の前提のほう。前提の側を新しい番号で更新する（既存 ADR は書き換えない約束。docs/adr/README.md）。

## 影響

- PJ の `gates.sh` は `gate()` を 1 行直す。`examples/projects/kumitate` と `examples/projects/aifactory` は直した。**`workspace/projects/` の私有 PJ 定義は git 追跡外なのでこの変更に含まれない**（人が 1 行直す。直すまでは `BASE-CHECK-LOG-MISSING` が出る）。
- `workflow/bin/run` と console は無改修。`^FAIL (\S+)` の読み取りも `work/gates/<名前>.log` の抜粋作りも変わらない。
- 検証は `workflow/tests/test_gates_base_red.py` の偽 VM ハーネス。本実行の赤が残り base 側が別ファイルに在ることを実測し、同名へ書き戻すと名指しで落ちる（退行注入で確認済み）。
