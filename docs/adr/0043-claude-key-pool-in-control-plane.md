# ADR 0043: Claude の鍵は PJ ではなく制御系のプールで持ち、take がフラグで選ぶ

- 状態: Accepted（ADR-0006 / ADR-0029 を補う。既存の決定は変えない）
- 日付: 2026-09-09

## 状況

Claude の鍵は「PJ ごと（`~/.config/sandbox/pj/<pj>.env`）と全体（`~/.config/sandbox/env`）に 1 本ずつ」だった（ADR-0006）。PR #46 でモデル系統別の `CLAUDE_CODE_OAUTH_TOKEN_FABLE` / `_OPUS` / `_SONNET` / `_HAIKU` が増えたが、値の出どころは同じ env ファイルのままで、鍵を足すたびに PJ の数だけ書き込む必要があった。

鍵は PJ の性質ではなく**契約の性質**（Fable が使える契約か、Opus / Sonnet が使える契約か、どれだけ使ったか）で分かれる。PJ に貼り付ける形だと、鍵を 1 本増やしても「どの PJ に配るか」を人が決めることになり、複数の鍵で使用量を分散させることもできない。

## 決定

1. **鍵の正本は制御系の `~/.config/sandbox/keys.json`（600）。** `state.json` の隣で、`SANDBOX_KEYS` で差し替えられる（テナント運用では `<t>.keys.json`）。書式は `{"keys":[{name, token, allow:{fable,other}, enabled, note, issued, last_used, uses}]}`。名前は一意（`[A-Za-z0-9._-]{1,40}`）。
2. **鍵は名前と 2 つのフラグを持つ。** `allow.fable`（Fable 用）と `allow.other`（Fable 以外＝ Opus / Sonnet / Haiku 用）。両方立ててもよい。
3. **選ぶのは `take` / `reset` / `reinject`。** 系統 2 群それぞれについて、`enabled` でフラグの合う鍵のうち **`last_used` が最も古いもの**を選ぶ（round-robin 相当。同値は名前順）。その task が前に使った鍵がまだ候補にあれば、それを使い続ける。選んだ名前は `state.json` の貸出項目に `keys: {fable, other}` として残す。
4. **VM への渡し方は #46 の経路をそのまま使う。** `inject_env` が `/run/sandbox/env` に `CLAUDE_CODE_OAUTH_TOKEN_FABLE`（fable 群）と `_OPUS` / `_SONNET` / `_HAIKU`（other 群。同じ値）を書き、名前だけを `CLAUDE_KEY_NAME_<系統>` で添える。runner の `agent_command` は変えない。run のログの `key=…` に `(pool: <名前>)` が付く。
5. **プールは env の鍵の「前」に立つだけで、置き換えない。** 候補が 1 本も無い系統は、これまでどおり PJ / 全体の env の鍵を使う。プールが空なら挙動は今までと同じ（互換）。無印の `CLAUDE_CODE_OAUTH_TOKEN` が env に無いときだけ、other 群の鍵で埋める（プールだけで運用できるように）。
6. **書き込みは CLI に集める。** `sandbox keys list|add|set|rm|token` だけが `keys.json` を書く。console の `core.py` は読み取り（`keys_view`）と、子プロセスとしての CLI 呼び出し（`keys_apply`）だけを持ち、`keys.json` を直接書かない（ADR-0015 の「状態を変えるのは CLI 経由」と同じ）。`keys.json` の更新は `state.json.lock` の中で行う（並列 take の直列化と同じロック）。
7. **鍵の値は名前と末尾 4 文字より先に出さない。** CLI の一覧・`--json`・console の応答・MCP `keys_list`・run の記録・ジョブのログのどれにも値を出さない。console の追加・差し替えは JobStore に載せない（JobStore は stdin を `jobs/<id>/stdin.txt` に、コマンド行を log に書くため）。MCP には読み取り（`keys_list`）だけを出す。
8. **無効化・削除の伝播は reinject。** `enabled` を落とす / 消したときは、その鍵を使っている貸出中の task に `sandbox reinject <task>` を起こす。動いている `claude -p` には触らず、次の起動から別の鍵になる（#46 の規則）。

## 理由

- **なぜ制御系に置くか。** `take` は制御系でしか走らない。鍵を 1 か所に集めれば、足す・止める・入れ替えるが 1 回で済み、どの鍵がどれだけ使われたかも 1 か所で分かる。PJ ごとに配ると、鍵を 1 本増やすたびに PJ の数だけ作業が要る。
- **なぜ 2 つのフラグか。** 契約の違いは実際には「Fable が使えるか」で割れている。系統 4 つぶんのフラグを持たせても、Opus / Sonnet / Haiku を別の鍵に分ける動機が今は無く、増えた分だけ設定を間違えやすくなる。必要になったら群を増やせばよい（`keys_pick <group>` の group を増やすだけ）。
- **なぜ `last_used` が最古のものか。** 使用量を鍵の間で均す一番単純な規則で、状態が 1 つ（`last_used`）で済む。「同じ task は同じ鍵を使い続ける」を先に見るので、1 回の run の中で鍵が入れ替わって認証がちぐはぐになることもない。
- **なぜ `aifactory_paths.py` に置かないか。** ADR-0016 が扱うのは `AIFACTORY_WORKSPACE`（projects / kanban / runs / logs / jobs）で、`~/.config/sandbox/` 系（`env` / `pj/*.env` / `state.json` / `last-used.json`）はそもそも管轄外で、bash 側の `CONF_DIR` から派生している。`keys.json` だけを別の流儀にする理由が無いので、`last-used.json` と同じく `STATE` の隣に置く。CONF_DIR 系を `aifactory_paths.py` に集約するかどうかは、この決定とは別の話として残す。
- **なぜ `bin/oss-check.sh` を変えないか。** oss-check は git 追跡ファイルと git の履歴だけを検査する仕組みで、ホーム下の `keys.json` はそもそも対象になり得ない。代わりに (a) 秘密のパターン（`sk-ant-…`）が既に検査対象であること、(b) `keys.json` が `CONF_DIR` 配下に 600 で作られることをテストで押さえる、の 2 つで守る。`.gitignore` に `keys.json` を足すのも、リポジトリ内にその名前で書く経路が無い以上、誤爆する汎用名を増やすだけなのでやらない。

## 影響

- `sandbox keys list|add|set|rm|token` が増える。`sandbox token show` の末尾にプールの本数と系統ごとの候補数が 1 行増える。`sandbox token set/clear/rotate` の動きは変えない（`rotate` は env の鍵だけを差し替え、プールは触らない）。
- `state.json` の貸出項目に `keys` が増える（読み手は無くても動く）。console の sandbox 画面の貸出行と MCP `sandbox_status` の `leases[].keys` に鍵の名前が出る。
- console に「鍵」画面（nav は sandbox とログの間、近道は `g k`）と `GET/POST /api/keys` が増える。MCP に `keys_list`（読み取り）が増える。
- 同じ鍵を複数の task が同時に使ってよい（上限は付けない）。同時使用数の制限は別の決定にする。
- `glue/bin/intake` は `ctl.env` の鍵のまま（プール対応は別票）。pull backend（macOS / Windows / Linux）の `take` も対象外。
