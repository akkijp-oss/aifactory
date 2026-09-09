#!/usr/bin/env bash
# kit/steps/pr-create.sh: 作業ブランチを push し、artifact を本文にして PR を作る。URL を ~/work/<task>/pr_url に置く
# runner から次の env で呼ばれる: PJ TASK RUN_DIR APP_DIR BASE BRANCH WORK WORKFLOW TITLE AUTO_MERGE(0/1)
set -euo pipefail
: "${TASK:?}" "${BASE:?}" "${BRANCH:?}" "${WORK:?}" "${WORKFLOW:?}" "${TITLE:?}"
sb() { sandbox ssh "$TASK" "$@"; }
# scrub: PR 本文に貼る前に、既知の秘密の形（Claude の長期トークン sk-ant-…、GitHub の ghs_/ghp_/gho_/ghu_/ghr_、
# KEY=値 の形の CLAUDE_CODE_OAUTH_TOKEN* / GH_TOKEN*）を伏せる。テスト出力や base 確認の転記に env の値が混ざった事故（2026-09-09 run 358）の再発防止。
# 判定の正本は workflow/kit/steps/scrub.sh（gates.sh も同じものを使う）
scrub() { bash "$(dirname "${BASH_SOURCE[0]}")/scrub.sh"; }
commits="$(sb "cd \$SANDBOX_APP_DIR && git log --oneline origin/$BASE..HEAD")"
[[ -n "$commits" ]] || { echo "[pr] コミットが無いので PR を作らない"; exit 1; }
sb "cd \$SANDBOX_APP_DIR && git push -q -u origin $BRANCH"
section() { local f=$1; sb "test -f $WORK/$f && { echo; echo \"## $f\"; echo; cat $WORK/$f; }" 2>/dev/null || true; }
# マージの主体は PJ の auto_merge 次第（ADR-0042）。読む人が「誰が押すのか」を PR の 1 行目で分かるようにする
if [[ "${AUTO_MERGE:-0}" == "1" ]]; then
  lead="ゲート緑・レビュー PASS・CI 緑なら aifactory が自動で \`$BASE\` へマージする（条件を満たさなければ開いたまま人間に渡す）。"
else
  lead="人間レビュー用の PR で、マージは人間が判断する。"
fi
scrub <<EOF | sb "cat > $WORK/pr-body.md"
aifactory sandbox（VM 内の agent）による自動作業。workflow: **$WORKFLOW**。$lead

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
# draft にするのは判定行（`=== base check:` より前）に FAIL があるときだけ。base 確認の記録に残る FAIL（base でも赤＝INFO に落ちた分）は数えない
# （数えると automerge が「draft」で見送る。2026-09-10 run 347。pr-automerge.sh と同じ読み方）
draft=""; sb "awk '/^=== /{exit} /^FAIL /{f=1} END{exit !f}' $WORK/gates.txt 2>/dev/null" && draft="--draft" || true
url="$(sb "cd \$SANDBOX_APP_DIR && gh pr create --base $BASE --head $BRANCH --title \"$TITLE\" --body-file $WORK/pr-body.md $draft 2>&1 | tail -1")"
echo "PR: $url"
sb "printf '%s\n' \"$url\" > $WORK/pr_url"
[[ "$url" == https://* ]]
