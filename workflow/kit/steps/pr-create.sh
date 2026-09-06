#!/usr/bin/env bash
# kit/steps/pr-create.sh: 作業ブランチを push し、artifact を本文にして PR を作る。URL を ~/work/<task>/pr_url に置く
# runner から次の env で呼ばれる: PJ TASK RUN_DIR APP_DIR BASE BRANCH WORK WORKFLOW TITLE
set -euo pipefail
: "${TASK:?}" "${BASE:?}" "${BRANCH:?}" "${WORK:?}" "${WORKFLOW:?}" "${TITLE:?}"
sb() { sandbox ssh "$TASK" "$@"; }
commits="$(sb "cd \$SANDBOX_APP_DIR && git log --oneline origin/$BASE..HEAD")"
[[ -n "$commits" ]] || { echo "[pr] コミットが無いので PR を作らない"; exit 1; }
sb "cd \$SANDBOX_APP_DIR && git push -q -u origin $BRANCH"
section() { local f=$1; sb "test -f $WORK/$f && { echo; echo \"## $f\"; echo; cat $WORK/$f; }" 2>/dev/null || true; }
sb "cat > $WORK/pr-body.md" <<EOF
aifactory sandbox（VM 内の agent）による自動作業。workflow: **$WORKFLOW**。人間レビュー用の PR で、マージは人間が判断する。

## コミット
\`\`\`
$commits
\`\`\`
$(section plan.md)
$(section report.md)
$(section review.md)
$(section gates.txt | head -40)

🤖 Generated with aifactory sandbox (task $TASK)
EOF
draft=""; sb "grep -q '^FAIL' $WORK/gates.txt 2>/dev/null" && draft="--draft" || true
url="$(sb "cd \$SANDBOX_APP_DIR && gh pr create --base $BASE --head $BRANCH --title \"$TITLE\" --body-file $WORK/pr-body.md $draft 2>&1 | tail -1")"
echo "PR: $url"
sb "printf '%s\n' \"$url\" > $WORK/pr_url"
[[ "$url" == https://* ]]
