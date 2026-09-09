# ADR 0041: チケットの添付は workspace の `kanban/attachments/<id>/` に置き、本文には書かない

- 状態: Accepted（ADR-0015 / ADR-0016 を補う。既存の決定は変えない）
- 日付: 2026-09-09

## 状況

チケットで agent に渡せるのは本文（Markdown の文字列）だけだった。`kb new --body` も `glue/bin/intake` も console の起票フォームも MCP `ticket_new` も文字列しか受け取らず、runner（`workflow/bin/run`）は take のあと `cat > ~/work/<id>/ticket.md` で本文だけを VM に置く。

そのため「この画面のここを直して」（スクリーンショット）「この表のとおりに」（CSV）「仕様書のこの節」（PDF）が言葉でしか伝えられない。VM の agent は Read ツールで画像（PNG / JPG）と PDF を読めるので、**ファイルが VM に届いてさえいれば見られる**。届ける口が無いだけだった。

`workflow/kit/workflows/feature-long.yml` の説明にある「plan.md がチケットに添付済みなら」も、実際には添付の仕組みではなく前回 run の `work/*.md` を運ぶ `carry_work()` の話で、名前だけが先にあった。

## 決定

1. **添付の正本は `$AIFACTORY_WORKSPACE/kanban/attachments/<チケット id>/<名前>`。** 本文（`tickets/<id>-<pj>-<slug>.md`）には添付のことを**書かない**。本文に「## 添付」節を自動生成すると、実体と本文の 2 か所が正本になって必ずずれる。一覧は表示側（`kb show` / `kb attachments` / console / MCP）が実体から導く。
2. **置き場の判断は `lib/aifactory_paths.py` の `ATTACHMENTS`（= `KB_ROOT / "attachments"`）に 1 つ**（ADR-0016）。新配置でも旧配置（LEGACY）でも kanban の隣に落ちる。
3. **添付の判定は `lib/aifactory_attachments.py` に 1 か所**（ADR-0015 の「読み書きの正本は 1 つ」と同じ考え）。名前の sanitize（パス区切り・`..`・制御文字を落とし、衝突は `-2`, `-3` …）と上限（1 ファイル 20 MiB / 1 チケット合計 100 MiB）はここだけが持ち、kb / runner / console / MCP はここを呼ぶ。標準ライブラリだけで動かす（ctl でも VM でも同じように使える）。
4. **runner は take のときに `~/work/<id>/attachments/` へ運び、置けた実績を `state.json` の `attachments` に残す。** 各 step の依頼文は「## チケット」の直後に `- 添付: ~/work/<id>/attachments/（<名前一覧>。…）` の 1 行を足す。案内は `state.json` の記録だけを根拠に出す（運べていない backend で嘘の案内をしない）。添付が無いチケットの依頼文は今までどおり 1 文字も変わらない。
5. **控えは既存の artifact 回収に乗せる。** `~/work/<id>/attachments/` は `release()` の `scp -r` でそのまま `runs/<run>/work/attachments/` に戻る。回収のために新しい経路を作らない（並行して動く #331 と同じ関数を作り直さないため）。
6. **秘密情報の検査はしない。** workspace は git 追跡外で `bin/oss-check.sh` の対象外（追跡されていないことだけを検査する）。トークンや鍵を添付しないのは人間の責任で、README と website にそう書く。
7. **第 1 段は Proxmox backend だけ。** pull backend（macOS / Windows / Linux）は `take()` を丸ごと持っていて `guest-put` 相当が要るので別票にする。運べていなければ `state.json` に `attachments` が無く、依頼文にも案内が出ない（自然に安全側に倒れる）。

## 理由

- **なぜ本文に書かないか。** 本文は人と LLM が書き換えるファイルで、実体の追加・削除と同期し続けられない。「正本は 1 つ」を守るなら、書ける場所は実体だけ。表示は導けばよい（ADR-0015 と同じ分業）。
- **なぜ workspace か。** チケット本文・kanban.db と同じ「運用データ」で、リポジトリに入れてよいものではない（ADR-0016）。画像は特に、公開リポジトリに混ぜたくないものが写り込む。
- **なぜ依頼文に埋め込まず、パスの案内だけにするか。** `build_prompt()` は入力をテキストとして `cat` して埋め込む。画像は埋め込めないし、埋め込めても依頼文が肥大する。VM のファイルとして置き、agent に Read で開かせるほうが安く確実。
- **なぜ上限を attach のときに効かせるか。** 運ぶとき（scp）や回収するとき（`scp -r`）に弾くと、VM を借りた後に失敗して無駄になる。入口で断れば、run の記録が 100 MiB を超えることもない。
- **なぜ本文と食い違ったら添付を優先させるか。** 添付（スクリーンショット・仕様書）は「実物」で、本文はそれを人が言葉にしたもの。食い違いは言葉のほうが古い場合が多い。ただし agent には食い違いを報告書に書かせ、人が気づけるようにする。

## 影響

- `kb attach <id> <file>...` / `kb attachments <id> [--json]` / `kb detach <id> <name>` / `kb new --attach FILE` が増える。`kb show` は添付があるときだけ末尾に一覧を足す（無いチケットの出力は変わらない）。history に `attachment` が残る。
- `console/lib/core.py` の `ticket_detail()` の返りに `attachments: [{name, size, type, added}]` が増える（HTTP のチケット画面と MCP `ticket_show` の両方）。
- `bin/oss-check.sh` の「追跡されてはいけない置き場」と `bin/migrate-workspace.sh` の移送対象に `kanban/attachments` が加わる。
- console の multipart 受信・サムネイル表示、MCP `ticket_attach`（base64）、`read_file` のバイナリ、intake の添付は**この決定の範囲だが未実装**（第 2 段。別票）。
