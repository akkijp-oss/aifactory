# 0005 Claude Code 認証は setup-token の長期トークンを take 時に tmpfs 注入

日付: 2026-09-05 / 状態: 採用

## 状況
VM 内で Claude Code を動かす（1エージェント1 sandbox）。認証方法として、通常の OAuth ログインをテンプレートに焼く / API キーで従量課金 / `claude setup-token` の長期トークン、があった。メンテナは Max プランを持ち、「setup-token または OAuth」と提示。

## 決定
Mac で `claude setup-token` を実行して得た長期トークンを `~/.config/sandbox/env` に1箇所だけ置き、`sandbox take` の時点で VM の `/run/sandbox/env`（tmpfs）へ書き込む。`/etc/profile.d/sandbox.sh` が login shell でこれを読み `CLAUDE_CODE_OAUTH_TOKEN` として export する。

## 理由
- 通常 OAuth の焼き込みは、複数クローンが同じリフレッシュトークンを回して衝突しうる
- API キーは並列数に制限がないが従量課金。まず Max プランで様子を見る（メンテナの判断）
- 長期トークンはリフレッシュ回転がないので、クローン間で共有しても衝突しない
- tmpfs に置けば巻き戻し（0003）と再起動で確実に消える。テンプレートに残らない

## 結果
- 有効期限切れは人間待ちになる（`STATUS.md` の人間待ち表に期限を書く）
- 複数 VM で同一トークンを同時使用することになる。端末数や同時接続の制限に当たったら、API キー方式へ切り替える（新 ADR）
- `sandbox ssh` はコマンド実行時も `bash -lc` で入り、profile.d を通す
