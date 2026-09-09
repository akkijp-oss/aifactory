#!/usr/bin/env bash
# kit/steps/pr-merge.sh: 既存 PR の head ブランチへ push し、ゲート・レビューの結果を PR コメントに残してから base へマージする
# runner から次の env で呼ばれる: PJ TASK RUN_DIR WORK BRANCH BASE PR_NUMBER MERGE_METHOD(merge|squash|rebase、既定 merge)
set -euo pipefail
: "${TASK:?}" "${WORK:?}" "${BRANCH:?}" "${BASE:?}" "${PR_NUMBER:?}"
METHOD="${MERGE_METHOD:-merge}"
sb() { sandbox ssh "$TASK" "$@"; }
# scrub: PR 本文に貼る前に、既知の秘密の形（Claude の長期トークン sk-ant-…、GitHub の ghs_/ghp_/gho_/ghu_/ghr_、
# KEY=値 の形の CLAUDE_CODE_OAUTH_TOKEN* / GH_TOKEN*）を伏せる。テスト出力や base 確認の転記に env の値が混ざった事故（2026-09-09 run 358）の再発防止。
# 判定の正本は workflow/kit/steps/scrub.sh（gates.sh も同じものを使う）
scrub() { bash "$(dirname "${BASH_SOURCE[0]}")/scrub.sh"; }
# コンフリクトマーカーが残っていたら止める
if sb "cd \$SANDBOX_APP_DIR && git grep -n -E '^(<<<<<<<|>>>>>>>)' -- . ':!*.md' | head -3" | grep -q .; then echo "[merge] コンフリクトマーカーが残っている"; exit 1; fi
# base を取り込み済みか（origin/base が HEAD の祖先か）
sb "cd \$SANDBOX_APP_DIR && git fetch -q origin $BASE && git merge-base --is-ancestor origin/$BASE HEAD" || { echo "[merge] origin/$BASE が取り込まれていない"; exit 1; }
sb "cd \$SANDBOX_APP_DIR && git push -q origin HEAD:$BRANCH"
section() { local f=$1; sb "test -f $WORK/$f && { echo; echo \"### $f\"; echo; cat $WORK/$f; }" 2>/dev/null || true; }
scrub <<EOF | sb "cat > $WORK/merge-comment.md"
aifactory sandbox がコンフリクトを解消し、ゲートとレビューを通して **$BASE** へマージします（workflow: merge-pr / task ${TASK}）。
$(section report.md)
$(section review.md)
$(section gates.txt | head -30)
EOF
sb "cd \$SANDBOX_APP_DIR && gh pr comment $PR_NUMBER --body-file $WORK/merge-comment.md >/dev/null"
# GitHub 側の mergeable 判定が更新されるのを待つ
for i in $(seq 1 12); do st="$(sb "cd \$SANDBOX_APP_DIR && gh pr view $PR_NUMBER --json mergeable -q .mergeable")"; [[ "$st" == MERGEABLE ]] && break; sleep 5; done
[[ "$st" == MERGEABLE ]] || { echo "[merge] PR がまだ MERGEABLE でない: $st"; exit 1; }
sb "cd \$SANDBOX_APP_DIR && gh pr merge $PR_NUMBER --$METHOD"
url="$(sb "cd \$SANDBOX_APP_DIR && gh pr view $PR_NUMBER --json url,state -q '\"\\(.url) \\(.state)\"'")"
echo "MERGED: $url"
sb "printf '%s\n' \"$url\" > $WORK/pr_url"
[[ "$url" == *MERGED ]]
