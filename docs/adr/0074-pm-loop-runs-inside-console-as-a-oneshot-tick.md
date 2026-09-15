# ADR 0074: 管理役（PM）は console の中の `pm_tick()` として動かす。tick は 1 段だけ進める oneshot で、マージの判断は持たない

- 状態: Accepted
- 日付: 2026-09-15
- チケット: 534

## 状況

aifactory を運転する「管理役」の仕事は、今のところ console の外（人間と手元のスクリプト）にある。
起票時は 6 つの仕事がすべて console の外にあると整理されていたが、2026-09-15 に `origin/develop` で実測すると
**半分は既に機械の側にあり、無いのは判断の部分だけ**だった。

| 仕事 | 実測した現在地 |
|---|---|
| 次に回す票を選ぶ | **既にある**。`kanban/bin/kb` の `cmd_next`（`status='todo'` を `id` 順で 1 件、`--pj` で絞る）／`core.ticket_next()`（`GET /api/next`）／`glue/bin/dispatch` の直列ループ |
| 起動前に票の前提を実測して申し送りを足す | 無い（人が grep して `ticket_action(append)`） |
| `ticket_run` する | **既にある**（`core.ticket_run()` core.py:1346。呼ぶ主体が外に居るだけ） |
| 止まった run を分類する | **事実の分類は既にある**（`core.run_outcome()` core.py:377、ADR-0025）。無いのは「その次に何をするか」 |
| PR 着地（CI 待ち・競合・マージ） | **既にある**。`workflow/kit/steps/pr-automerge.sh`（ADR-0042） |
| マージ後の base の CI を見届ける | 無い |

常駐の前例も、起票時に挙がった `console/jobs/` の JobStore + `console/systemd/aifactory-console.service` だけではない。
**timer + oneshot の前例が既にある**: `sandbox/templates/systemd/aifactory-resume.timer`（5 分ごと）が
`aifactory-resume.service`（`Type=oneshot`、`ExecStart=python3 @@REPO@@/glue/bin/dispatch --resume-paused`）を起こし、
`sandbox/bin/install.sh --systemd` が配っている。定期実行の死活監視・再起動・ログ置き場（`journalctl` と
`$AIFACTORY_WORKSPACE/logs/`）は、この形で既に解けている。

したがって本 ADR で決めるのは「何を新しく作るか」ではなく、**既にある部品のどれをどう繋ぐか**である。

## 決定

### 決定 1: PM のループは `core.pm_tick()` という関数にし、起動は `aifactory-pm.timer` + oneshot service に載せる

`pm_tick()` は `console/lib/core.py` の関数として置く。`sandbox/templates/systemd/aifactory-pm.timer` と
`aifactory-pm.service`（`Type=oneshot`）を `aifactory-resume.*` と同型で足し、薄い CLI 入口（引数を JSON にするだけ）を呼ぶ。
launchd 側も `console/launchd/` の plist と同型で置く。多重実行は既存の `jobs/.lock`（`core._flock()` core.py:880）で直列化し、
timer からの tick と Web / MCP からの手動 tick が重ならないようにする。

**tick 自身は JobStore の job にしない。** tick は数秒で終わる（読んで、高々 1 本 `ticket_run()` を呼ぶ）。
tick が起こした run は `ticket_run()` が既に `JobStore.start("kb-run", ...)` に載せるので、長い仕事は今までどおり job として見える。

理由: 新規デーモンにすると死活監視・再起動・ログ置き場・設定の配り方を全部新しく作ることになる。
timer + oneshot なら、そのすべてが `aifactory-resume` で既に動いている形の写しで済む。判定を `core.py` に置くのは ADR-0015
（口は薄く、読み書きの正本は core）の通りで、Web と MCP と timer の 3 経路が同じ関数を呼べる。

**採らなかった案**:
- **新しい常駐デーモン**（PM 専用プロセスが眠りながら回る）。上記の運用一式を新設することになる。5 分の粒度で足りる仕事に常駐は要らない。
- **console プロセス内の `threading.Timer`**。console を止めると PM も止まり、MCP（stdio、別プロセス）からは動いているかどうかが見えない。
  多重起動の判定が `jobs/.lock` の外に出て、二重起動の防ぎ方が 2 か所になる。
- **`glue/bin/dispatch` を拡張する**。dispatch は「todo が尽きるまで直列に回す」前提のループで、
  待たずに 1 段だけ進める tick とは制御構造が違う。混ぜると `--once` / `--max` / `--resume-paused` の意味がさらに増える。

### 決定 2: 4 状態は保存せず、既存の記録から導く

`idle` / `waiting` / `landing` / `blocked` の 4 つで足りる。ただし **PM は状態を持たない**。毎 tick、次の材料から導く:

- `idle`: `kb next` が空でなく、かつその PJ に実行中の `kb-run` job が無い（＝回せる）。`kb next` も空なら「回す票が無い」
- `waiting`: `kb-run` job が running、または run の `run_outcome().reason == "running"`
- `landing`: run の `state.json` の `next` が `pr` か `automerge`。**PM は観察するだけで駆動しない**（決定 6）
- `blocked`: 票の `status == "blocked"`、または終わった run の `run_outcome().reason` が
  `pr_created` / `loop_limit` / `step_failed` / `step_timeout` / `human_abandoned` などで、PM が再投入しないと決めたもの

`pause` は状態ではなく設定（決定 4 の `mode`）。**tick は 1 段だけ進める**: `idle` なら 1 本起こす、`blocked` なら再投入の可否を
判断する、それ以外は何もしないで返る。run の完了を待つのは tick の外（timer の次の発火）。

理由: ADR-0025 と同じ流儀。状態を別に持つと、人が `kb` を直接叩いた・run が落ちた・VM が返ったといった外の変化とずれ、
「PM の思っている状態」と「実際」の 2 つを突き合わせる仕事が新たに生まれる。導けば過去の run にも同じ見方が効く。

**採らなかった案**: 5 状態目（`landing` を `ci_wait` と `merging` に割る／`proposed` を状態にする）。
前者は `pr-automerge.sh` の中の進行で、制御系からは `state.json` の粒度でしか見えない（実測: runner は工程の合否しか書かない）。
後者は「人の承認待ち」であって PM の状態機械の段ではなく、決定 5 の判断ログの行が持てば足りる。

### 決定 3: モードは `propose` / `auto` の 2 つ。`auto` でも止まる線は 3 本で、**すべて「新しい run を起こすか」にだけ掛かる**

`propose` は判断を判断ログに `proposed` として書いて止まる（`pm_steer(approve=...)` で実行）。`auto` は書いて実行する。
PM はマージしないので（決定 6）、止まる線が掛かるのは `ticket_run()` を呼ぶかどうかだけである。

**線① 本番に出る操作**: ブランチ名では判定しない。判定は **`project.yml` に配布経路の設定があるか**で行う。
★実測: `workflow/kit/schema/project.schema.json` の properties は
`app_dir / auto_merge / backend / base_branch / computer_use / display / display_name / facts / forbidden / gates /
hotfix_base / known_red_gates / name / prepare / repo / review_points / stack / url / worker / workflow_overrides` で、
**配布経路に当たるキーは無い**。したがって本 ADR は「そのキーを持つ PJ では `auto` でも `propose` に落ちる」とだけ決め、
**キーが定義されるまでどの PJ もこの線には当たらない**。キーの新設は本票の範囲外（後続票か別票で決める）。
ブランチ名で代用しない理由: `hotfix_base` は PR の宛先であって配布ではなく、宛先を配布の代理にすると
「develop に入れば本番」という事実に反する規則になる（ADR-0042 は main への昇格を人間に残している）。

**線② 同じ票が同じ理由で 2 回落ちた**: 「続ける」の機構を名指しで決める。
★**runner のループ延長（`severity_bonus`）ではない。PM が `core.ticket_run(tid, {"from_step": "implement"})` で
新しい run を起こす**（core.py:1355 が `kb run --from` に渡す）。実測上これしか無い:
`docs.yml` の review は `on_fail: {goto: implement, max_loops: 1, else: human}`、`run:1039` は
`n < t["max_loops"] + self.severity_bonus(...)`、加点は 1 遷移につき 1 回きり（ADR-0053）。
**1 本の run の中で review→implement は最大 2 回**で、3 回は起きえない。
「同じ理由」は**機械で読める事実だけ**で比べる: `run_outcome()` の `reason` + `stopped_step` + `gates.txt` の `FAIL <名>` の集合。

- **gates 由来**: FAIL の集合が直前の run と同じなら止める（`blocked`）。違えば上限内で続ける。
- **review 由来**: `review.md` の指摘は自由文で、理由の同一性を機械で判定できない（推測で埋めない＝ADR-0025）。
  よって**署名ではなく回数で切る**。既定は「PM 起動の再投入は同じ票につき 2 回まで、超えたら `blocked`」。

**線③ 危ない差分**: 再投入の前に `git diff --name-status origin/<base>...<wip>` を見て、`D` 行（ファイル削除）か
パスに `migration` を含む変更があれば、`auto` でも `propose` に落ちる。`project.yml` の `forbidden` は自由文なので
機械判定せず、人に見せる材料として判断ログの `facts` に転記するだけにする。

理由: 3 本とも「取り返しがつくか」で分けている。マージは既に人と `auto_merge` の手にあり（決定 6）、
PM が取り返しのつかないことをする唯一の経路が「新しい run を起こす」（VM と鍵を焼き、wip を作る）ため。

**採らなかった案**: 「2 回落ちたら一律で止める」。起票時の実測例（ある私有 PJ で、review FAIL のたびに別の指摘が付き、
続けた結果 PASS して着地した）がこの規則では着地しない。逆に「理由が違えば無制限に続ける」も採らない——
review の理由の同一性は機械で読めないので、無制限は「止まらない」と同義になる。

### 決定 4: 横やりの口は 4 つ。名前をここで確定させる

core（ADR-0015 のとおり判定はここだけ）:

| core の関数 | HTTP | MCP tool |
|---|---|---|
| `pm_status()` | `GET /api/pm` | `pm_status` |
| `pm_config(mode=None, pj=None, interval_min=None)` | `POST /api/pm/config` | `pm_config` |
| `pm_steer(next=None, skip=None, pause=False, resume=False, approve=None)` | `POST /api/pm/steer` | `pm_steer` |
| `pm_tick(dry=False)` | `POST /api/pm/tick` | `pm_tick` |

`bin/console` は既存の平らな `ep == "..."` 分岐に 4 行足すだけ、`bin/mcp` は同名で包むだけ（どちらも判定を持たない）。
画面は `#/pm` の 1 枚で、`console/static/index.html` の `<nav class="rail">` に `data-nav="pm"` を 1 項目足す。
更新は既存の 5 秒ポーリング（`setInterval(refreshNav, 5000)`）に乗る——SSE も WebSocket も足さない。

**`pm_steer` は走行中の run を止めない。** `next=<id>` / `skip=<id>` / `pause` / `resume` / `approve=<decision_id>` は
いずれも**次の tick に効く**。走行中を止めるのは既存の `job_stop` の役目である。
理由: 走っている run を割り込みで止めると VM と鍵を焼き、wip の保全（ADR-0067）も途中になる。
同じことを 2 つの口から別の強さでできるようにしない。

設定の置き場は `lib/aifactory_paths.py` に `PM_CONFIG`（`WORKSPACE / "pm.json"`）を **1 か所だけ**足す（ADR-0016）。
書き方は ADR-0065 / 0073 の型（検証 → 退避 → 一時ファイル → `os.replace`）に揃え、変更は `logs/config-changes.jsonl` に 1 行残す。

**採らなかった案**: `pm_steer` に「今の run を止めてから次へ」を持たせる案（上記のとおり `job_stop` と二重になる）。
`pm_status` を `overview` に相乗りさせる案（`overview` は板の要約で、PM の判断材料とは更新頻度も読む相手も違う）。

### 決定 5: 判断ログは `logs/pm-decisions.jsonl`。1 行 1 判断で、**理由は語彙で書き、文言は書かない**

置き場は `paths.LOGS / "pm-decisions.jsonl"`。`config-changes.jsonl`（`core.CONFIG_CHANGES` core.py:2139）とまったく同じ置き方で、
新しい置き場の判断を増やさない（ADR-0016）。1 行の形:

```json
{"at": "...", "pj": "...", "ticket": 534, "run": "...", "state": "blocked",
 "action": "requeue", "reason_code": "same_gate_fails", "facts": {...}, "mode": "auto", "by": "tick"}
```

`reason_code` の語彙（この表に無い理由を PM が作らない）:
`no_todo` / `picked_next` / `run_running` / `landing_observed` / `merged_observed` / `requeued` /
`same_gate_fails` / `review_retry_limit` / `release_path` / `risky_diff` / `forbidden_hint` /
`proposed` / `approved` / `skipped_by_steer` / `paused`。
`facts` には機械で読めた事実だけを入れる（`reason` / `stopped_step` / `FAIL` の集合 / 削除ファイル数など）。
**日本語の説明文は書かない**——表示の文言は `console/static/strings.js` 側が `reason_code` から作る（ADR-0025）。
保持は回転せず追記のみ。画面は末尾 N 行だけ読む（`core.config_changes(limit)` core.py:2237 と同型で、末尾 200 行を読んで新しい順に切る）。

理由: 画面の主役は「何をしたか」ではなく「なぜそうしたか」。語彙に落とせば、後から数えられる（「`review_retry_limit` が
今月 12 件」）し、文言を直しても過去の行が古びない。

**採らなかった案**: `kanban.db` に表を足す案。kanban.db は `kb` の正本で、console が直接書く口を作ると
「状態を変えるのは既存 CLI 経由だけ」（ADR-0013 / ADR-0039）を破る。PM 専用の DB を新設する案も、
読むのが「末尾 N 行」だけなので費用に見合わない。

### 決定 6: PM はマージ条件を持たない（起案 (B)）。マージは runner の `automerge` 工程のまま

★**ADR-0042 の判定を PM に写さない。** 実測: 判定の実体は `workflow/kit/steps/pr-automerge.sh` にだけあり、
6 条件の失敗はすべて `nomerge()`（31 行）に集約されている。呼ぶのは `workflow/bin/run` の `code:` 工程で、
渡す条件は `run.automerge_env()`（run:961 付近）が PJ の `auto_merge` と workflow の形から組み立てる。
`core.py` に automerge の判定は**無い**（唯一の言及は 435〜436 行で、`step == "automerge"` の失敗を読み出すだけ）。

**PM が読むのは `core.run_outcome()`（core.py:377）の事実だけ**である:

- `reason == "merged"`（runner が自分でマージした。`state.json` の `merged` が根拠）→ 着地。PM はマージ後の見届けへ進む
- `reason == "pr_created"`（PR は出たがマージされていない）→ `blocked`。`automerge_error`（435〜436 行）を判断ログの `facts` に転記して人へ渡す

`auto_merge` を書いていない PJ では、`pr-automerge.sh` の工程自体が `SKIPPABLE_CODE_STEPS`（`workflow/bin/run:335`）として
runner に飛ばされる。**この PJ では PM もマージしない。** マージしてよいかの判断の持ち主は `project.yml` の `auto_merge`、
すなわち PJ 側であって PM ではない。PM は「PR ができた」を `blocked` として人に渡す。

★**したがって `pm_land()` は作らない。** 後続票（#538）の「`pm_land()` が ADR-0042 の判定を呼んでいることを示す」という
完了条件は、**「`landing` を観察し、`merged` / `pr_created` を判断ログに記録することを示す」に読み替える**。

**採らなかった案**:
- **(A) 判定を `core.py` に移して shell から呼び戻す**。判定は VM の中で `sb` 経由・VM の App token を持つ `gh` で動いており、
  制御系の Python から呼ぶと sandbox 内実行との往復と `gh` 認証の二重化が要る。二重管理は消えるが、その費用を払う理由が無い。
- **(C) 判定だけを切り出して shell / Python の両方から使う 1 か所にする**。PM がその判定を**必要としない**（決定 6 の結論）ので、
  共用のための抽象を先に作ることになる。必要になったときに (C) へ動かせる形は保たれている（PM は `run_outcome` しか読まないため）。

## 結果（トレードオフ）

- 反応は tick の間隔ぶん遅れる。run が終わってから次が起きるまで最大 1 間隔（既定 5 分）空く。即時性が要るなら `pm_tick` を手で叩く
- 再投入は新しい run なので、VM と鍵を取り直す（`--from` は wip ブランチから続けるが、VM は焼き直し）。回数の上限（決定 3 の線②）はこの代償に対する歯止め
- review 由来の失敗は回数でしか切れない。「同じ指摘で 3 回落ちた」と「毎回違う指摘で 3 回落ちた」を PM は区別できない。区別できるのは gates だけ
- `landing` は観察しかできない。`pr-automerge.sh` の内側（CI 待ちの残り本数など）は制御系から見えず、見えるのは工程の合否だけ
- 止まる線①は、配布経路のキーが定義されるまで**一度も発火しない**。これは穴だが、ブランチ名で代用するより安全側に倒れている（何も止めないのではなく、他の 2 本と人の目が残る）

## 未確認

- **(a) 制御系から base（`develop`）の CI を読めるか。** VM の中の `gh` は App token を使うが、制御系側の `gh` 認証は実測していない。
  「マージ後の base の CI を見届ける」はこれが読めるかに依存する。**#537（tick 票）の最初に確かめる**前提とし、
  読めない場合は見届けを PM の仕事から外して人に残す
- **(b) 起票時の実測例「review FAIL を 3 周して着地した」が (ア) 記憶違いで 2 周だったのか、(イ) 3 周目が `--from` の別 run だったのか。**
  私有 PJ の記録はこのリポジトリに無く、確かめられない。**設計は (イ) として書いた**（決定 3 の線②）。
  (ア) だったとしても結論は変わらない——どちらにせよ「続ける」の機構は PM の再投入であって `severity_bonus` ではない

## 既存 ADR との整合

- **ADR-0013 / 0039**（状態を変えるのは既存 CLI 経由）: PM も `kb next` / `kb run` を呼ぶだけで、kanban.db を直接書かない（決定 5）
- **ADR-0015**（口は 2 つ、正本は `core.py`）: 判定は `pm_*` の 4 関数だけに置き、`bin/console` と `bin/mcp` は同名で包む（決定 4）
- **ADR-0016**（置き場の判断は `lib/aifactory_paths.py` に 1 つ）: 新設は `PM_CONFIG` の 1 行のみ。判断ログは既存の `LOGS` の下（決定 5）
- **ADR-0025**（事実を残し、読み方は導く側で決める）: 4 状態は保存せず導く（決定 2）。判断ログは `reason_code` と `facts` だけで文言を持たない（決定 5）
- **ADR-0042**（条件を 2 か所に持たない）: マージ条件を本 ADR は 1 つも定義していない。PM は `run_outcome()` の結果だけ読む（決定 6）
- **ADR-0053**（`severity: minor` の 1 回だけの加点）: PM は `severity_bonus` を変えず、依存もしない。PM の再投入は runner のループ計数の外で、
  `max_loops` と加点の意味は今のまま（決定 3 の線②）
- **ADR-0070**（制御系は main を追い、未配備を機械で見せる）: PM は `repo_status()` を**読む側**で、配備の判断は持たない
