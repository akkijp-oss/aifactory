# ADR 0029: 制御系の `gh` トークンは GitHub App から都度払い出す／`kind` は種別、`workflow` は実行方法

- 状態: Accepted
- 日付: 2026-09-08

## 状況

`start: pr` の workflow（`merge-pr`）は、VM を借りる前に制御系で `gh pr view <pr>` を呼んで PR の head / base を調べる。ここが使う `GH_TOKEN` は、制御系 LXC の `~/.config/aifactory/ctl.env` に人が入れる静的トークン（PAT）の前提だった。

実際には、その雛形は `GH_TOKEN=`（空）で作られ、誰も入れていなかった。さらに MCP（`console/bin/mcp`）は ssh の非ログイン環境で起動するので `ctl.env` を読まず、`GH_TOKEN` という変数自体が無い。結果、`kb run 236 --workflow merge-pr` は経路によらず `gh auth login` を促されて落ちた（2026-09-08、チケット 249）。

一方、同じホストには GitHub App（ADR-0008）が設定済みで、`sandbox gh-app token <pj>` がその PJ のリポジトリだけに効く 1 時間トークンを払い出せる。VM の中の `gh` は既にこれを使っている。制御系だけが使っていなかった。

また `kb run --workflow X` はチケットの `kind` を X に書き換えていた（失敗しても戻さない）。`kind=bug` のチケットを 1 回 `merge-pr` で回すと、台帳上そのチケットは永久に `merge-pr` になる。

## 決定

1. 制御系で `gh` を使う前に、`GH_TOKEN` が無い／空なら `sandbox gh-app token <pj>` を試し、取れたら runner 自身の環境に入れる（`workflow/bin/run` の `ensure_gh_token()`）。子プロセスは既存どおり環境を継承する。払い出せなければ、`gh` の失敗理由と App の失敗理由を **1 つのメッセージ**にして人に返す。トークンの値はログにも `state.json` にも出さない。
2. 払い出しは runner の 1 か所だけで行う。`console/lib/core.py` の `child_env()` ではやらない（PJ を知らず、`kb list` のような子まで App API を叩くことになる）。
3. secrets の入口は `ctl.env` に一元化する。`console/bin/mcp` は起動時に `core.load_ctl_env()` で `~/.config/aifactory/ctl.env`（`AIFACTORY_CTL_ENV` で差し替え可）を読み、**未設定の変数だけ**補う。systemd の console は今までどおり `EnvironmentFile` で読む。どちらの経路から起こしても同じ環境になる。
4. `ctl.env` の `GH_TOKEN` は **App が無いときのフォールバック**に格下げする。空でよい。
5. チケットの `kind` は「何の仕事か」、run の `workflow` は「今回どう回したか」。`kb run --workflow X` は `kind` を書き換えず、`history` に `workflow → X` を残し、`runs/<run>/state.json` の `workflow` を正とする。`--workflow` 無しの `--resume` は `kind` ではなくその `state.json` の `workflow` で再開する。

## 理由

- 静的 PAT は、人が用意し、期限を管理し、リポジトリ全体に効く。App の installation token は 1 時間・対象リポジトリ限定で、既にこのシステムの標準（ADR-0008）。制御系だけ別の方式を要求する理由がない。
- 「無ければ取りに行く」を runner の中でやると、MCP / console API / 手打ちのどの経路でも同じ結果になる。経路ごとに環境を揃える作業が要らない。
- 期限 1 時間で足りるのは、制御系の `gh` が `Run.__init__` の 1 回だけだから。将来 step 中に制御系の `gh` を増やすなら払い出し直しが要る。
- `kind` と `workflow` を同じ列で表すと、1 回の実行が台帳の分類を壊す。分類は人が `kb set --kind` で変えるもので、実行の副作用で変わってよいものではない。

## 結果（トレードオフ）

- 制御系が GitHub App API を叩く回数が増える（`start: pr` の run ごとに 1 回）。App 未設定なら今までどおり静的 `GH_TOKEN` で動く。
- `sandbox gh-app token <pj>` は `~/.config/sandbox/pj/<pj>.env` の `GH_REPO` を見る。これが `project.yml` の `repo` と食い違う PJ では払い出したトークンが効かない。runner は突き合わせをせず、`gh` の失敗理由をそのまま見せる。
- `ctl.env` を読む口が 2 つ（systemd と mcp）になる。mcp 側は「無いものを補う」だけなので、手で `GH_TOKEN=... console/bin/mcp` と渡した値は勝つ。
- 既に `kind` が書き換わった台帳（例: チケット 236 の `merge-pr`）は自動では戻さない。人が `kb set <id> --kind bug` で直す。
