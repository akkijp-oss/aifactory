#!/usr/bin/env bash
# kit/steps/scrub.sh: 標準入力の文章から既知の秘密の形を伏せて標準出力に出す（PR 本文・PR コメント・gates の転記に使う）。
#   - Claude Code の長期トークン: sk-ant-<種別>-<本体> → 先頭だけ残して ****
#   - GitHub のトークン: ghs_ / ghp_ / gho_ / ghu_ / ghr_ / github_pat_ → 先頭だけ残して ****
#   - KEY=値 の形: CLAUDE_CODE_OAUTH_TOKEN*=… / GH_TOKEN*=… / ANTHROPIC_API_KEY=… → KEY=****
# 伏せるだけで行は消さない（何が起きたかは読めるように）。判定はここ 1 か所に置き、呼ぶ側は scrub.sh に通すだけ。
# GNU / BSD どちらの sed でも動く書き方（\b を使わない）にしてある。2026-09-09 の run 358（テスト出力に env の値が混ざり
# gates.txt 経由で PR 本文に載る寸前だった）から
set -euo pipefail
sed -E \
  -e 's/(sk-ant-[A-Za-z0-9]+-[A-Za-z0-9_-]{4})[A-Za-z0-9_-]{8,}/\1****/g' \
  -e 's/(^|[^A-Za-z0-9_])(gh[spour]_[A-Za-z0-9]{4})[A-Za-z0-9]{8,}/\1\2****/g' \
  -e 's/(^|[^A-Za-z0-9_])(github_pat_[A-Za-z0-9_]{4})[A-Za-z0-9_]{8,}/\1\2****/g' \
  -e 's/(^|[^A-Za-z0-9_])((CLAUDE_CODE_OAUTH_TOKEN[A-Z_]*|GH_TOKEN[A-Z_]*|ANTHROPIC_API_KEY|SANDBOX_CLAUDE_TOKEN|SANDBOX_GH_TOKEN)=)[^[:space:],"'"'"']+/\1\2****/g'
