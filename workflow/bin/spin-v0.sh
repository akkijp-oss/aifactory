#!/usr/bin/env bash
# spin-v0.sh: 最小の AI developer workflow（v0 スパイク。BUILD.md Step 6 の 1 周を「コード」で回す）
#   take → VM 内で claude -p（依頼文ファイル）→ ゲートをコードで実行 → 赤なら結果を Claude に戻して再試行 → push → PR → 所見 → release
#   使い方: workflow/bin/spin-v0.sh <pj> <task-id> <prompt-file> <branch-slug> [base-branch=develop]
#   前提: sandbox CLI（take/ssh/release）、PJ 定義の gates.sh（$AIFACTORY_WORKSPACE/projects/<pj>/ か examples/projects/<pj>/）、PJ の CLAUDE トークン、GitHub App
#   出力: $AIFACTORY_WORKSPACE/runs/<date>-<pj>-<task-id>.md に依頼文・ゲート結果・PR URL・所要秒数を残す
#   歴史的な v0 スパイク。今は workflow/bin/run（v1）が正で、これは記録のために残している
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PJ=${1:?pj}; TASK=${2:?task-id}; PROMPT_FILE=${3:?prompt-file}; SLUG=${4:?branch-slug}; BASE=${5:-develop}
MAX_LOOPS=${MAX_LOOPS:-2}
# モデル: 依頼文 2 行目の `class: judgment|research|coding` を workflow/routes.env で引く。環境変数 CLAUDE_MODEL があればそれが優先（一回限り）
# shellcheck disable=SC1091
source "$HERE/workflow/kit/routes.env"
CLASS="$(sed -n '2p' "$PROMPT_FILE" | sed -nE 's/^class:[[:space:]]*([a-z]+).*/\1/p')"; CLASS="${CLASS:-coding}"
_m="MODEL_$CLASS"; MODEL="${CLAUDE_MODEL:-${!_m:-$MODEL_default}}"
MODEL_OPT="--model $MODEL"
# 置き場は lib/aifactory_paths.py が決める（ADR-0016）
PJ_DIR="$(python3 -c "import sys; sys.path.insert(0, '$HERE/lib'); import aifactory_paths as p; print(p.project_dir('$PJ') or '')")"
GATES="$PJ_DIR/gates.sh"; [[ -n "$PJ_DIR" && -f "$GATES" ]] || { echo "[error] $PJ の gates.sh が無い（$AIFACTORY_WORKSPACE/projects/$PJ/ か examples/projects/$PJ/）" >&2; exit 1; }
RUN_DIR="$(python3 -c "import sys; sys.path.insert(0, '$HERE/lib'); import aifactory_paths as p; print(p.RUNS)")"; mkdir -p "$RUN_DIR"
RUN="$RUN_DIR/$(date +%Y-%m-%d)-$PJ-$TASK.md"
BRANCH="sandbox/$TASK-$SLUG"
t0=$(date +%s); lap() { echo $(( $(date +%s) - t0 )); }
log() { echo "[spin $PJ/$TASK $(lap)s] $*"; }
sb() { sandbox ssh "$TASK" "$@"; }

{
  echo "# spin-v0: $PJ / task $TASK"
  echo; echo "- 日時: $(date -Iseconds)"; echo "- ブランチ: $BRANCH → $BASE"; echo "- クラス / モデル: $CLASS / $MODEL"
  echo; echo "## 依頼文"; echo; echo '```'; cat "$PROMPT_FILE"; echo '```'
} > "$RUN"

RESUME="${RESUME:-0}"   # 1 なら take〜最初の claude を飛ばし、貸出中の VM のゲートから再開する（途中で落ちた run の続き）
# 1. take
if (( RESUME )); then log "resume: take/branch/claude(loop 0) を飛ばす"; t_take=0; else
log "take"
sandbox take "$PJ" "$TASK" | tail -1
t_take=$(lap)
fi
sandbox ssh "$TASK" 'true'
scp -q -i "$HOME/.ssh/conf.d/aifactory/sb_ed25519" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$GATES" "dev@$(jq -r --arg id "$TASK" '.[$id].ip' "$HOME/.config/sandbox/state.json"):/home/dev/gates.sh"
(( RESUME )) || scp -q -i "$HOME/.ssh/conf.d/aifactory/sb_ed25519" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$PROMPT_FILE" "dev@$(jq -r --arg id "$TASK" '.[$id].ip' "$HOME/.config/sandbox/state.json"):/home/dev/prompt.md"

# 2. ブランチ
(( RESUME )) || sb "cd \$SANDBOX_APP_DIR && git fetch -q origin $BASE && git checkout -q -B $BRANCH origin/$BASE && git log -1 --format='base: %h %s'"

# 3. claude → gates ループ
loop=0; gates_out=""; ok=0
while (( loop <= MAX_LOOPS )); do
  if (( loop == 0 )); then
    msg="/home/dev/prompt.md"
  else
    sb "cat > /home/dev/retry.md" <<EOF
前回の変更に対してゲートを実行したところ、次の結果になった。赤のゲートのログの要点も示す。原因を直して、もう一度ゲートが全部緑になるようにしてほしい。修正はコミットまで行うこと。

ゲート結果:
$gates_out

$(for g in $(echo "$gates_out" | awk '/^FAIL/{print $2}'); do echo "=== $g.log (tail)"; sb "tail -40 ~/gates/$g.log" 2>/dev/null; done)
EOF
    msg="/home/dev/retry.md"
  fi
  if (( RESUME && loop == 0 )); then log "claude (loop 0) skipped (resume)"; else
  log "claude (loop $loop)"
  t_c0=$(date +%s)
  sb "cd \$SANDBOX_APP_DIR && claude -p \"\$(cat $msg)\" $MODEL_OPT --dangerously-skip-permissions --output-format text 2>&1 | tail -60" | tee -a "$RUN.claude-$loop.log" | tail -15
  log "claude done in $(( $(date +%s) - t_c0 ))s"
  fi
  # 追跡済みファイルの未コミット変更だけ Claude の代わりにコミットする（作業を落とさない）。
  # 未追跡ファイルは拾わない（テストが生成した DB 等の混入事故。2026-09-06 に turso の .db を拾いかけた）。一覧だけ記録する
  sb "cd \$SANDBOX_APP_DIR && git add -u && (git diff --cached --quiet || git commit -q -m 'sandbox: uncommitted changes by agent (loop $loop)')"
  untracked="$(sb "cd \$SANDBOX_APP_DIR && git status --porcelain | grep '^??' | head -20" || true)"
  [[ -n "$untracked" ]] && { echo "[warn] 未追跡ファイルあり（push しない）:"; echo "$untracked"; { echo; echo "## 未追跡ファイル（loop ${loop}、push 対象外）"; echo; echo '```'; echo "$untracked"; echo '```'; } >> "$RUN"; }
  log "gates (loop $loop)"
  gates_out="$(sb 'bash ~/gates.sh' || true)"
  echo "$gates_out"
  { echo; echo "## ゲート結果（loop ${loop}）"; echo; echo '```'; echo "$gates_out"; echo '```'; } >> "$RUN"
  if ! echo "$gates_out" | grep -q '^FAIL'; then ok=1; break; fi
  loop=$((loop+1))
done

# 4. 差分と push / PR
diff_stat="$(sb "cd \$SANDBOX_APP_DIR && git diff --stat origin/$BASE...HEAD | tail -5")"
commits="$(sb "cd \$SANDBOX_APP_DIR && git log --oneline origin/$BASE..HEAD")"
{ echo; echo "## 差分"; echo; echo '```'; echo "$commits"; echo; echo "$diff_stat"; echo '```'; } >> "$RUN"
pr_url=""
if [[ -n "$commits" ]]; then
  log "push + PR"
  sb "cd \$SANDBOX_APP_DIR && git push -q -u origin $BRANCH"
  title="$(head -1 "$PROMPT_FILE" | sed 's/^# *//' | cut -c1-70)"
  body_file=/home/dev/pr-body.md
  sb "cat > $body_file" <<EOF
aifactory sandbox（VM 内の Claude Code）による自動作業。人間レビュー用の PR。

## 依頼
$(sed -n '1,20p' "$PROMPT_FILE")

## ゲート（VM 内で実行）
\`\`\`
$gates_out
\`\`\`
$([[ $ok == 1 ]] || echo "⚠️ ゲートに赤が残っている（$MAX_LOOPS 回の再試行後）。マージ不可")

🤖 Generated with aifactory sandbox (task $TASK)
EOF
  pr_url="$(sb "cd \$SANDBOX_APP_DIR && gh pr create --base $BASE --head $BRANCH --title \"$title\" --body-file $body_file $([[ $ok == 1 ]] || echo --draft) 2>&1 | tail -1")"
  echo "PR: $pr_url"
else
  log "コミットなし（Claude が変更しなかった）"
fi

# 5. 所見 + release
{
  echo; echo "## 結果"; echo
  echo "- ゲート: $([[ $ok == 1 ]] && echo 全緑 || echo 赤あり)（ループ $loop 回）"
  echo "- PR: ${pr_url:-なし}"
  echo "- 所要: take まで ${t_take}s / 全体 $(lap)s"
} >> "$RUN"
log "release"
sandbox release "$TASK" | tail -1
echo "run log: $RUN"
[[ $ok == 1 ]]
