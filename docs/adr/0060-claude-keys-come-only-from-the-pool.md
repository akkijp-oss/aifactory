# ADR 0060: VM に渡す Claude の鍵の出どころは鍵プールだけにし、`sandbox` の env ファイル経路（`token set claude` と空プール時のフォールバック）を廃止する

- 状態: Accepted（ADR-0006 の「PJ ごとの env ファイルに Claude の鍵を置く」を廃止。ADR-0045 の「非推奨・互換のため残す」と ADR-0046 の「プールが空のときだけ env の鍵」を取り下げる。ADR-0029 の `token rotate` は GitHub 用と ctl.env 用に絞る）
- 日付: 2026-09-11
- メンテナの判断（2026-09-10）: 「sandbox コマンドから注入する経路は、後方互換性不要前提で削除する。VM の鍵の入手経路は console の「鍵」画面（`#/keys`）の情報から取得できるようにする。コストよりも品質」

## 状況

Claude の鍵の置き場は 3 段階で変わってきた。

1. ADR-0005 / ADR-0006: `~/.config/sandbox/env`（全体）と `~/.config/sandbox/pj/<pj>.env`（PJ 別）に `CLAUDE_CODE_OAUTH_TOKEN` を置き、`sandbox token set <pj>` で保存、`take` が VM の tmpfs に注入する。
2. ADR-0044: 制御系の鍵プール（`keys.json`。console の「鍵」画面と `sandbox keys`）を足す。プールは env の鍵の「前」に立つだけで、置き換えない。
3. ADR-0045 / ADR-0046: プールを正本にし、`token set <pj> claude` を非推奨にする。ただし互換のため動かし、プールが空のときだけ env の鍵に落ちる。

この「互換のための残り」が、鍵の出どころを 1 つに見せる邪魔になっていた。

- `sandbox token set <pj> claude` は警告を出しながら保存できるので、古い手順書どおりに操作した人の鍵が env ファイルに残る。プールが 1 本でもあれば使われないが、プールを全部消した瞬間に env の鍵で run が走る。「鍵」画面で全部止めたのに止まらない、が起こりうる。
- `sandbox` の中に `_src_claude` / `SANDBOX_CLAUDE_TOKEN` / `load_pj` の Claude 分岐 / `_pool_apply_locked` の空プール分岐 / `token show` の env の鍵の表示、と Claude の鍵を env から引く経路が 5 か所残り、runner（Proxmox）と pull backend（`keys pick`。チケット 391）の両方がその規則を前提にしていた。
- 2026-09-10 には、プロセス環境に残った古い鍵が pull backend の guest まで流れて使われ続けた（チケット 391）。修正は入ったが、「env や環境から鍵を拾う経路が 1 つでもあれば、消したはずの鍵が使われる」ことの実例である。

「鍵」画面（`http://ctl.<t>.sb.internal:8765/#/keys`）に載っている鍵だけが VM に渡る、という単純な事実を成り立たせるには、env ファイル経路を消すしかない。互換を保つ相手（Mac 運用の `token set`）は 2026-09-07 に運用を止めており、実機（main テナント）では ADR-0046 の時点で env ファイルの Claude の鍵を全部消してある。

## 決定

1. **VM に渡す Claude の鍵の出どころは鍵プール（`keys.json`）だけ。** `sandbox take` / `reset` / `reinject` / `keys pick` はプールで選んだ鍵だけを VM（pull backend は `runtime.env`）に書く。要る用途の鍵が無ければ、プールが空でも env に鍵があっても「鍵なし:」で止まる（ADR-0046 の一時停止と再開はそのまま）。
2. **`sandbox` は env ファイルとプロセス環境の Claude の鍵を読んでも捨てる。** 起動時と env / pj ファイルを `source` した直後、鍵を選ぶ直前に `CLAUDE_CODE_OAUTH_TOKEN*` を unset する（`drop_claude_env`）。`SANDBOX_CLAUDE_TOKEN`（一回限りの上書き）は無くす。`GH_TOKEN` の層（env → pj → `SANDBOX_GH_TOKEN`）は ADR-0006 のまま。
3. **`sandbox token set|clear` は GitHub トークン専用。** kind を省略した場合も含めて `claude` / `claude:<系統>` は保存せずに止まり、`sandbox keys add`（または「鍵」画面）と、intake 用なら `token rotate claude` を案内する。
4. **`sandbox token rotate` は 2 つに分ける。** `rotate gh` は ADR-0029 のまま（global・`GH_TOKEN` を持つ全 PJ・`ctl.env` を更新し、console を restart、貸出中 VM に `reinject --all`）。`rotate claude[:<系統>]` は制御系の `ctl.env`（intake が `claude -p` を呼ぶのに使う鍵）だけを更新して console を restart する。env / pj ファイルにも貸出中の VM にも触らず、制御系でないホストでは止まる。
5. **`sandbox token show` は出どころを言い直す。** VM に渡す鍵はプールから、と 1 行で言い、`ctl.env` の intake 用の鍵（マスク・発行からの日数）、GitHub トークンの出どころ、プールの本数（`keys.json` が無ければ 0 本と、run が一時停止する旨）を出す。env / pj ファイルに `CLAUDE_CODE_OAUTH_TOKEN*=` の行が残っていれば `[stale]` でファイルと変数名を挙げ、消し方（`sed`）を添える。値は出さない。
6. **runner は系統の鍵だけを使う。** `agent_command` の前置きは `CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN_<系統>"` で、無印の鍵に落ちない（Windows の PowerShell 版も同じ）。VM にその系統の鍵が無ければ probe が `none` を返し、runner は claude を起動せずに `failure: "key"`（人間待ち。戻しの回数は消費しない。理由に系統と `sandbox reinject <task>` を書く）で止める。通常は take が要る用途をそろえる（ADR-0046）ので、これは `--from` で別のモデルの工程から回した等の例外を認証エラーまで進ませないための保険である。
7. **console の sandbox 画面の「Claude の鍵」列はプールの状態だけを言う。** 「鍵プール」/「鍵プール（Fable 用だけ）」/「鍵プール（Opus・Sonnet 用だけ）」/「未登録（run は一時停止）」の 4 つで、env ファイルは見ない。「鍵」画面の説明文から「設定ファイルの鍵を使う」を消し、「合う鍵が無い run は一時停止し、登録すると自動で回し直す」に替える。
8. **`intake` の鍵（`ctl.env`）は対象外のまま。** ADR-0044 / 0045 / 0046 の「別票」を引き継ぐ。VM の鍵ではないので、この決定の「出どころは 1 つ」は VM に渡る鍵についての約束である。プールから選ぶようにするのは別の決定にする。

## 理由

- **なぜ互換を切るか。** 互換のために残した経路は「使われないはず」の経路で、使われたときに気づけない。プールが正本（ADR-0045）で、鍵が無ければ止まる（ADR-0046）と決めた以上、env の鍵が run を動かす道は矛盾でしかない。Mac 運用の `token set` は止まっており、残す相手がいない。
- **なぜ読んでも捨てるか（消さないか）。** `sandbox` が人の設定ファイルを勝手に書き換えるのは避ける（`token rotate` も行置換に限っている。ADR-0029）。読まないだけなら残っていても害は無く、`token show` の `[stale]` で片付けを促せば足りる。
- **なぜ `rotate claude` を残すか。** intake の鍵は `ctl.env` にあり、切れれば差し替えが要る。手で編集して console を restart する手順は事故が起きやすい（2026-09-07 に Mac 側で `token set` してしまい制御系に反映されない事故が起きた。ADR-0029）。名前を変えると手順書の互換が崩れるだけなので `rotate claude` のまま、対象を `ctl.env` に絞る。
- **なぜ runner が起動前に止めるか。** 系統の鍵が無いまま `claude -p` を起動すると、認証エラーとして `failure: "key"` になるまで工程 1 つ分の時間を使う。probe は元々鍵の名前を読んでいるので、`none` を見て止めるのは 1 分岐で済む。
- **なぜ console の列を 4 値にするか。** env を見なくなったので、PJ ごとの違いは無く、言えるのは「プールに何がそろっているか」だけ。「未設定」を「未登録（run は一時停止）」にしたのは、ADR-0046 で決めた振る舞いをその場で言うため。

## 結果

- 鍵の追加・停止・入れ替え・削除は「鍵」画面（または `sandbox keys`）だけで完結し、そこに無い鍵が VM に渡ることは無い。
- `sandbox token set <pj>` を使う古い手順書は全部書き直した（README / OPERATIONS / STATUS / BUILD / `env.example` / console の README / docs の HTML 2 つ / website の ja・en）。`25-control-lxc.sh` は制御系の `~/.config/sandbox/env` に `CLAUDE_CODE_OAUTH_TOKEN=` を書かなくなった。
- `_src_claude` / `SANDBOX_CLAUDE_TOKEN` / `env_has_claude_key`（console）は無くなり、`key_source` の値は `pool` / `pool_fable_only` / `pool_other_only` / `none`。API `/api/sandbox` の `key_source` を読む側は値の集合が変わる。
- 既存の env / pj ファイルに残った `CLAUDE_CODE_OAUTH_TOKEN*=` の行は効かない。`sandbox token show` が `[stale]` で挙げるので、そのとおり `sed -i '/^CLAUDE_CODE_OAUTH_TOKEN/d' <file>` で消す。
- テストは「プールが空でも env・プロセスの鍵で take / pick を通さない」「`token set claude` は何も書かない」「`rotate claude` は `ctl.env` だけ」「probe が `none` なら起動前に `failure: key`」「console の列は 4 値」を固定した。
