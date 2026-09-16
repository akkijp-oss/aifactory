### Added
- **「鍵」画面に鍵ごとの残量（利用枠）が出る**（ADR-0087）。5 時間枠 / 7 日枠（全体）/ 7 日枠（Fable 専用）ごとの残り %・リセットまでの時間・窓の始点 → 終点・「このペースだと約 n で枯渇」の見込みを、鍵ごとのカードとバーで出す。表の下に鍵ごとの折れ線「残量の推移」（窓と 24 時間 / 7 日を切り替え）、一覧に「残量」列（いちばん逼迫している窓）。鍵切れ（認証が通らない）は run が止まる前にここに出る
- **観測は `lib/aifactory_keys_quota.py probe`**。鍵ごとに極小の messages を 1 回送って応答の `anthropic-ratelimit-unified-*` を読み、`keys.json` の隣の `keys-quota.db`（600）に現在値と履歴（30 日）を残す。安いモデル（既定 haiku）で 5 分ごと、Fable 許可の鍵は 15 分ごとに Fable でも叩く（Fable の枠は Fable で問い合わせたときだけ返る。安い回はその値を消さない）。鍵の値は記録に出ない。`show` / `history` で CLI からも読める
- **systemd の timer `aifactory-keys-probe.timer`（5 分ごと）**。`sandbox/bin/install.sh --systemd` が他の timer と一緒に登録する（制御系で打ち直す）。console の「いま調べる」で、その場でも観測できる（ジョブ）
- **API / MCP**: `GET /api/keys` の `keys[].quota`、`GET /api/keys/history`、`POST /api/keys/probe`。MCP `keys_list` の `keys[].quota` で AI の運転係も残量を読める
