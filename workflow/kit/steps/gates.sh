#!/usr/bin/env bash
# kit/steps/gates.sh: PJ のゲート（templates/<pj>/<gates>）を VM で走らせ、結果を ~/work/<task>/gates.txt に置く。全緑なら 0
# runner から次の env で呼ばれる: PJ TASK RUN_DIR PROJECT_DIR GATES WORK
set -uo pipefail
: "${TASK:?}" "${PROJECT_DIR:?}" "${GATES:?}" "${WORK:?}"
KEY="$HOME/.ssh/conf.d/aifactory/sb_ed25519"
ip="$(jq -r --arg id "$TASK" '.[$id].ip' "$HOME/.config/sandbox/state.json")"
scp -q -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$PROJECT_DIR/$GATES" "dev@$ip:/home/dev/gates.sh"
out="$(sandbox ssh "$TASK" "bash ~/gates.sh")"; rc=$?
# base で既に赤いゲート（project.yml の known_red_gates、env KNOWN_RED に空白区切り）は INFO に格下げする
for k in ${KNOWN_RED:-}; do out="$(sed -E "s/^FAIL $k( |$)/INFO $k red (known on base; not a gate)\1/" <<< "$out")"; done
printf '%s\n' "$out"
sandbox ssh "$TASK" "mkdir -p $WORK && cat > $WORK/gates.txt" <<< "$out"
if grep -q '^FAIL' <<< "$out"; then
  # 赤のログ末尾も添えて implementer に戻せるようにする
  for g in $(awk '/^FAIL/{print $2}' <<< "$out"); do
    { echo; echo "=== $g.log (tail 60)"; sandbox ssh "$TASK" "tail -60 ~/gates/$g.log"; } | sandbox ssh "$TASK" "cat >> $WORK/gates.txt"
  done
  exit 1
fi
exit 0
