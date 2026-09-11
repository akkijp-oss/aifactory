# ADR 0062: 実効モデルと分岐の解決規則は `lib/aifactory_workflow.py` の 1 か所に置き、設定画面は静的な定義だけから解く

- 状態: Accepted
- 日付: 2026-09-11

## 状況

設定画面（`#/config`）の workflow 一覧は、各工程を `{id, role, code}` に間引いて「名前 / 流れ」の 2 列に出していた
（`console/lib/core.py` の `config_view()`、`console/static/app.js` の `viewConfig()`）。読めないものが 4 つあった。

1. **工程の中身**。`brief` / `inputs` / `outputs` / `timeout_min` / `model_class` は API の時点で捨てられていた。
2. **分岐**。`feature` は `sync` が成功すれば `resolve` を飛ばして `pr` へ行くのに、画面は定義の順に
   `sync → resolve → pr` と並べるので、`resolve` が常に回るように読めた。戻り（`on_fail` の `goto`）も人間待ちも出ない。
3. **実効モデル**。`routes.env` の 4 行と役割名の一覧が出るだけで、工程 → クラス → 経路 → モデルの対応が読めない。
4. **code 工程にモデルが無いこと**。`role` の無い工程は `model_for()` を通らないが、画面はそれを言わない。

このうち 3 と 2 の規則は `workflow/bin/run` の中にしかなかった。

- `Runner.model_for()`: 環境変数 `CLAUDE_MODEL` > `step.model_class`（無ければ役割の既定クラス）> `MODEL_<クラス>` > `MODEL_default`
- `Runner.transition()`: `on_pass` / `on_fail` があればその組、無ければ成功は `next`・失敗は `human`。値が無ければ成功 `end`・失敗 `human`

役割 → 既定クラスの対応表に至っては、`run` の辞書リテラル・`kit/routes.env` の冒頭コメント・各 `kit/roles/<role>.md` の
「クラス:」の 3 か所に人手で重複していた。設定画面のために console 側へ 4 つ目の写しを作れば、
チケットが禁じた「画面に手書きコピーして不整合を増やす」をこちらから作ることになる。

## 決定

1. **解決規則の正本を `lib/aifactory_workflow.py` に 1 つ置く**。`lib/` は runner と console の両方が `sys.path` に
   入れている既存の共有置き場（`aifactory_paths` などと同じ）。置くのは状態を持たない純関数だけにする。
   - `ROLE_CLASS`: 役割 → 既定のモデルクラス（これが正本。`routes.env` と `roles/*.md` の記述は説明）
   - `resolve_model(step, routes, env_model)`: 実効モデルと、それがどこ（`env` / `routes` / `default`）から決まったか
   - `transition_of(step, ok)`: 行き先と、それがどのキー（`on_pass` / `on_fail` / `next` / 既定）から決まったか
   - `main_path(steps)`: 先頭から成功の遷移だけを辿った並び。ここに載らない工程が「失敗したときだけ回る工程」
2. **`workflow/bin/run` は委譲する**。`model_for()` / `transition()` は行き先とモデルの判断を共有関数に任せ、
   例外の型（未知の役割・経路表に既定が無い場合の `KeyError`）は現行のまま保つ。
   **状態を持つ処理は runner に残す**: 戻れる回数の数え上げ（`state.json` の `loops`）と、
   `severity: minor` の加点（ADR-0053）。共有側に持ち込むと純関数でなくなり、設定画面が run の状態に依存する。
3. **設定画面は静的な定義だけから解く**。`/api/config` は `workflow/kit/workflows/*.yml` と `routes.env` と
   `workflow.schema.json` だけを読み、工程ごとに `model_resolved` / `transitions` / `timeout_min` /
   `timeout_default` / `unknown_keys` を足して返す。**生の step をそのまま通し**、算出値を足すだけにする
   （未知のキーが API の時点で消えないように）。`timeout_min` の既定値も schema の `default` から読み、4 つ目の写しを作らない。
4. **`CLAUDE_MODEL` は console 側の環境変数を読まない**。これは run を起こすプロセスの環境変数で、console のそれとは別物。
   読めば嘘になる。API は `env_override_possible: true` という事実だけを返し、画面は「実行時に上書きされることがあり、
   その値は設定からは分かりません」と書いて、実績（過去 run で実際に使われたモデル）は統計画面へ導く。
   **定義から解ける値と過去 run の実績を同じ欄に混ぜない。**
5. **code 工程は `model_resolved: null`** とし、画面は「この工程は機械が実行します。モデルは使いません。」を必ず出す。
   モデル名は 1 つも出さない。
6. **成功の道（`main_path`）と条件付きの工程を画面で分ける**。一覧でも詳細でも、`resolve` のような工程を
   成功の並びに混ぜない。混ぜると「常に回る」と読めてしまう（これがチケットの症状そのもの）。
7. **`/api/config` は増やすだけで、消す・意味を変える変更はしない**。MCP の `config` ツールも同じ形を使う公開形なので、
   既存のキー（`workflows[].steps[].id/role/code`・`routes`・`roles`・`git` ほか）はそのまま残す。
8. **1 件の定義が読めなくても endpoint 全体を落とさない**。`parse_error` / `errors`（schema 検査）/ `unknown_keys` に
   入れて残りを返し、画面は隠さずそのまま出す。

## 結果

- 一覧 → workflow 詳細 → 工程詳細 が本物の `<a href>` でつながり、Tab と Enter だけで往復できる。
- `feature` の `sync` 成功 → `pr`、失敗 → `resolve`、ゲート失敗 → `implement`、上限後 `human` が
  定義と一致することを `console/tests/test_config_detail.py` が検査する。写しが増えれば
  `workflow/tests/test_model_resolution.py`（runner と共有関数の答えが一致すること）が落ちる。
- 戻せる回数そのものは定義の `max_loops` を出すが、ADR-0053 の加点は **数字を書かず**
  「軽微なときは 1 回だけ増えることがあります」と規則の存在だけを添える（5 つ目の写しを作らないため）。
- この画面は読み取りだけで、ジョブも runner も設定変更も起こさない。POST は 1 本も足していない。
- モデルの編集（#416）は同じ `lib/aifactory_workflow.py` の関数を再利用する前提。閲覧と保存で二重に計算しない。
- 配備物（`ctl.main.sb.internal`）とこの checkout の定義が同じかどうかは、この変更では確かめられない。
  差があれば配備側の問題として別に扱う。
