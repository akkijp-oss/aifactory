# 0008 GitHub への push / PR 権限は GitHub App の installation token を take のたびに払い出す

日付: 2026-09-06 / 状態: 採用（0005 / 0006 の `GH_TOKEN` の出どころを定める。静的トークンは後方互換として残す）

## 状況
VM 内のエージェントは対象リポジトリへブランチを push し PR を作る。そのための `GH_TOKEN` をどう用意するかで、メンテナは「gh コマンドで適切な権限の範囲のものを払い出せないか」と問い、選択肢を示したうえで **B: GitHub App** を選んだ（2026-09-06）。
- `gh auth token` は既存の OAuth トークンで、スコープ `repo` = 全リポジトリ読み書き。絞れない
- Fine-grained PAT はリポジトリ・権限を絞れるが発行 API が無く、Web での作成と期限管理が人手になる
- GitHub App は作成と install だけ人手で、以後は API で **1 時間有効・リポジトリ単位・権限 3 つ**のトークンを機械が払い出せる

## 決定
1. App `aifactory-sandbox`（所有者 = メンテナの個人アカウント、**public**）。権限は Contents RW / Pull requests RW / Metadata R のみ。Webhook 無効。public にするのは、個人所有の非公開 App が所有者以外（org）に install できないため。public でも他者が得られるのは自分のリポジトリへの権限だけ。`sandbox/bin/gh-app-setup` がマニフェストで作り、App ID と秘密鍵を `~/.config/sandbox/gh-app/`（600）に保存する
2. 対象リポジトリごとに install する（リポジトリの所有者（個人 / org）が複数あればそれぞれに）。PJ とリポジトリの対応は `~/.config/sandbox/pj/<pj>.env` の `GH_REPO`
3. `sandbox take` / `reinject` / `gh-app refresh` のたびに、CLI が JWT（RS256、openssl）→ `POST /app/installations/{id}/access_tokens`（`repositories: [<repo>]` に限定）で installation token を取り、`/run/sandbox/env` の `GH_TOKEN` に注入する。期限は `GH_TOKEN_EXPIRES_AT` として併記
4. App が未設定なら従来どおり `pj/<pj>.env` / `env` の静的 `GH_TOKEN` を使う（後方互換）。実行時の環境変数 `GH_TOKEN=...` は常に最優先（0006）
5. 1 時間で切れるので、Mac の launchd（`templates/launchd/com.aifactory.sandbox.gh-refresh.plist`）が 45 分ごとに `sandbox gh-app refresh` を走らせ、貸出中の VM 全部を払い出し直す

## 理由
- 権限がリポジトリ単位・書き込み 2 種に閉じる。漏れても 1 時間で失効する
- 人手は App 作成と install の 2 回だけ。以後トークンの期限切れで人間待ちにならない
- 秘密鍵は Mac にだけ置き、VM には短期トークンしか渡らない（0005 の「テンプレートに焼かない・tmpfs に置く」と整合）

## 結果（トレードオフ）
- VM 内の作業が 1 時間を超えると `GH_TOKEN` が切れる。launchd の refresh が前提。切れたときの症状は `git push` の 401 で、`sandbox gh-app refresh` で復旧
- App の install 対象に無いリポジトリでは `take` が失敗する（`sandbox gh-app status` で確認）
- テンプレート焼き込み時の clone は引き続き `gh auth token`（読み取りだけで足りるが、App の install 前でも動くようにするため）
