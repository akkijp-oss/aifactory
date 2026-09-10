### Removed
- **`sandbox` の env ファイル経由の Claude の鍵を廃止**（ADR-0060。後方互換なし）。VM に渡る Claude の鍵の出どころは鍵プール（console の「鍵」画面 `#/keys` / `sandbox keys`）だけになり、`~/.config/sandbox/env` と `pj/<pj>.env` の `CLAUDE_CODE_OAUTH_TOKEN*`・`SANDBOX_CLAUDE_TOKEN`・「プールが空なら env の鍵」のフォールバックは無くなった（残っている行は読まれない。`sandbox token show` が `[stale]` で場所と消し方を言う）。`sandbox token set|clear` は `gh` 専用になり、`claude` を指定すると保存せずに止まって `sandbox keys add` を案内する。要る用途の鍵がプールに無ければ、プールが空でも `take` / `keys pick` は「鍵なし」で止まり、run は一時停止する

### Changed
- **`sandbox token rotate claude` は制御系の `ctl.env`（intake 用の鍵）だけを差し替える**。env / pj ファイルにも貸出中の VM にも触らず、制御系でないホストでは止まる。`rotate gh` は今までどおり（global・全 PJ・`ctl.env` の更新、console restart、`reinject --all`）
- runner は工程のモデルの系統の鍵（`CLAUDE_CODE_OAUTH_TOKEN_<系統>`）だけで claude を起動し、無印の鍵には落ちない。VM にその系統の鍵が無ければ起動せずに `failure: key`（人間待ち。理由に `sandbox reinject <task>` を書く）で止まる
- console の sandbox 画面の「Claude の鍵」列は「鍵プール / 鍵プール（Fable 用だけ）/ 鍵プール（Opus・Sonnet 用だけ）/ 未登録（run は一時停止）」の 4 つになった（API `/api/sandbox` の `key_source` は `pool` / `pool_fable_only` / `pool_other_only` / `none`）。「鍵」画面の説明から「設定ファイルの鍵を使う」を消した
- README / OPERATIONS / STATUS / BUILD / `env.example` / console の README / docs の HTML / website（ja・en）から `sandbox token set <pj>` で Claude の鍵を置く手順を消し、鍵プールだけの手順にした。`25-control-lxc.sh` は制御系の `~/.config/sandbox/env` に `CLAUDE_CODE_OAUTH_TOKEN=` を書かない
