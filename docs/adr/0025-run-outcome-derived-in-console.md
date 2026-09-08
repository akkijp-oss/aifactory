# ADR 0025: 実行記録の停止理由はコンソール側で導く

- 状態: Accepted
- 日付: 2026-09-07

## 決定

実行詳細の冒頭に出す「結果・止まった工程・理由の在り処」は、コンソール（`console/lib/core.py` の `run_outcome`）が既存の記録から導く。runner（`workflow/bin/run`）と `state.json` の形式は変えない。

材料は `state.json` の `history`（工程ごとの合否）・`loops`（戻した回数）・`result`・`pr_url` と、`runs/<name>/` にあるファイルの有無、それに `work/gates.txt` の `FAIL <ゲート名>` 行だけとする。理由は `pr_created` / `loop_limit` / `step_failed` / `ended` / `waiting` / `running` / `not_started` / `v0` / `unknown` のいずれかに落とし、導けないものは `unknown`（画面では「記録にありません」）にする。停止理由を推測で埋めない。

ファイルの目的別の分類（成果物 / 工程のログ / その他）も同じ場所で導く。成果物の名前はファイル名の決め打ちではなく、workflow 定義（`kit/workflows/<name>.yml` の各 step の `role` と `outputs`）から引く。workflow が増えても分類が崩れないようにするため。

## 理由

runner は工程が落ちた理由の自由文を次の工程への申し送り（`retry_note`）にしか使わず、`state.json` には残さない。停止理由を state に書き足すと runner・kb・過去の記録の互換の話になり、表示の改善に runner の変更が要ることになる。表示側で導けば、過去の run にも同じ要約が出る。

## 制約

- 自由文の要約（agent が何と言って止まったか）は出せない。出せるのは工程名・回数・赤いゲートの名前と、読むべきファイルへの導線までとする
- 「{n} 回続けて通らず」の回数は `history` の中で当該工程が ng だった回数を数える（`loops` は戻した回数で 1 少ない）
- 記録とログの添字がずれている run（古い記録、手で消したログ）では「理由を書いたファイルは残っていません。」と出し、画面を落とさない

runner が停止の理由を短い自由文で `state.json` に書く案は次段階とする。書かれるようになれば `run_outcome` はそれを優先して使えばよく、この決定は覆らない。
