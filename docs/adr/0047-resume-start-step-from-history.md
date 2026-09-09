# ADR 0047: `--resume` の開始工程は `next` ではなく工程履歴から決め、続きが無い run は VM に触る前に止める

- 状態: Accepted（ADR-0036 決定 1 の「`--resume` の分岐には手を入れない」を上書きする）
- 日付: 2026-09-10
- チケット: 338

## 状況

`state.json` の `next` は「次の遷移先」で、`human` / `end` も入る。runner の工程ループは `while cur not in ("end","human")` なので、
`--resume`（貸出中の VM で続きを回す）が `next` をそのまま開始工程にすると、**失敗して止まった run では必ず 1 工程も走らない**。

実際に、Mac の pull backend で準備（provision）が失敗した run（`history: []` / `next: human` / lease 保持）を、人が原因を直してから
`--resume` したところ、「準備をやり直した → 準備できた → 成果物の確認後に VM を返却 → result: human / PR なし」とだけ出て終わった。
工程を 1 つも走らせず、回収する成果物も無い。結局チケットを開き直して新しい VM を取り直し、VM の準備（約 75 秒 + provision）を払い直した。

`--resume` は「準備で落ちた回をその場で拾い直す」ための口なのに、その用途で必ず空回りしていた。
ゲートの戻しを使い切って `human` になった run を、人が base を直してから続ける口としても同じ理由で使えなかった。

## 決定

1. **`--resume` の開始工程は `state.json` の工程履歴（`history`）から導く**（純関数 `resume_start(state, steps)`）。
   - `next` が `human` / `end` 以外（Ctrl-C や runner が落ちた途中）→ そのまま `next`
   - `next` が `human` で履歴が空（1 工程も終えていない＝準備で落ちた）→ workflow の**先頭工程**
   - `next` が `human` で履歴あり → **最後に走った工程**（gates 超過なら `gates`、implement の時間上限なら `implement`）
   - 決まった工程が今の workflow に無ければ止める（工程名が変わった古い記録を推し測らない）
2. **続きが無い run は VM に触る前（`take` の前）に止める。** 履歴の末尾が成功で `resume_step` も無い（PR まで出ていて
   `automerge` がマージしなかった回など）と `next: end` が該当する。黙って VM を返却するより、止まって人に選ばせる方が安い。
3. **`loops`（戻した回数）は引き継ぐ。** 続きであって回し直しではないため。gates 超過の後に gates がまた赤なら即 `human` になるが、
   それは意図どおり（人が同じ直しを繰り返さない）。数え直したいなら `--from`（新しい VM）を使う。
4. **前回の終わり方（`result` / `finished` / `error` / `failure` / `elapsed_s` / `resume_step` / `pr_url` / `wip_branch`）は
   `--resume` の開始時に消す**（`Run.__init__` の 1 か所。従来 macOS backend だけが一部を消していた）。
   消さないと kb と console が古い `finished` と `result` を見て、続いている run を終わったものとして読む。

## 対象外

- `--from`（**新しい VM** で指定の工程からやり直す。ADR-0036）の経路は変えない。`resume_step` の記録の仕方、`wip_branch` からの
  checkout、`loops` を数え直す規則もそのまま。`--from` と `--resume` を同時に指定したら止める既存の検査も残す。
- `--resume` は VM が貸出中のときだけ使える（sandbox backend では準備の失敗時に VM を返すので、その場合は `--from` か
  `kb reopen` → `kb run`）。ADR-0036 のこの区別は変えない。
- pull backend の「工程履歴があれば provision をやり直さない」判定は正しく動いているので触らない。

## 結果

- 準備が失敗した run は、人が原因を直して `--resume` すれば先頭工程から走る。VM の準備を払い直さずに済む。
- base の赤でゲートが止まった run は、人が base を直してから `--resume`（貸出中）か `--from gates`（返却済み）で gates から続けられる。
- 「何もせず VM を返却して終了」する経路は無くなった。続きが無いときは記録を消さずに止まる。
