#!/usr/bin/env bash
# kit/steps/pr-automerge.sh: pr-create.sh が作った PR の条件を確かめ、全部満たすときだけ base へマージする（ADR-0041）
# runner から次の env で呼ばれる: PJ TASK RUN_DIR WORK BASE BRANCH RUN_NAME HAS_REVIEW(0/1)
#   AUTO_MERGE_METHOD(merge|squash|rebase) AUTO_MERGE_WAIT_MIN AUTO_MERGE_DELETE_BRANCH(0/1) AUTO_MERGE_REQUIRE_CHECKS(0/1)
# 待ちの長さはテストのために env で上書きできる: AUTOMERGE_POLL_S(30) AUTOMERGE_ZERO_CHECKS_GRACE_S(180) AUTOMERGE_MERGEABLE_POLL_S(5)
#   AUTOMERGE_WAIT_S（既定は AUTO_MERGE_WAIT_MIN 分。テストが 20 分待たずに「終わらない」側を確かめるため）
# マージしたら $WORK/merged.json を書き、最終行に `MERGED: <sha> <url>` を出して 0 で終わる（runner が state.json に転記する）。
# マージしないのは失敗ではなく正常な終わり方の 1 つ。理由を **最終行** に `NOMERGE: <理由>` として出し、1 で終わる（runner が error に使う）
set -euo pipefail
: "${TASK:?}" "${WORK:?}" "${BASE:?}" "${BRANCH:?}"
METHOD="${AUTO_MERGE_METHOD:-merge}"
WAIT_MIN="${AUTO_MERGE_WAIT_MIN:-20}"
REQUIRE_CHECKS="${AUTO_MERGE_REQUIRE_CHECKS:-1}"
DELETE_BRANCH="${AUTO_MERGE_DELETE_BRANCH:-1}"
HAS_REVIEW="${HAS_REVIEW:-0}"
RUN_NAME="${RUN_NAME:-}"
POLL_S="${AUTOMERGE_POLL_S:-30}"
GRACE_S="${AUTOMERGE_ZERO_CHECKS_GRACE_S:-180}"
MERGEABLE_POLL_S="${AUTOMERGE_MERGEABLE_POLL_S:-5}"

sb() { sandbox ssh "$TASK" "$@"; }
ghq() { sb "cd \$SANDBOX_APP_DIR && gh $1"; }        # VM の中で gh を叩く（GitHub App の installation token）
nomerge() { echo "NOMERGE: $1"; exit 1; }            # 必ずこれが最終行になるよう、呼んだ後に何も出さない

# ---------- PR 番号（pr-create.sh が置いた $WORK/pr_url）
url="$(sb "cat $WORK/pr_url 2>/dev/null || true" | tr -d '\r' | head -1)"
num="$(printf '%s\n' "$url" | sed -n 's#.*/pull/\([0-9][0-9]*\).*#\1#p')"
[[ -n "$num" ]] || nomerge "pr_url が無いので PR を特定できない"

# ---------- ゲート（`=== base check:` より前の判定行だけを見る。INFO＝base でも赤は可。ADR-0038）
gates="$(sb "cat $WORK/gates.txt 2>/dev/null || true")"
fails="$(printf '%s\n' "$gates" | awk '/^=== /{exit} /^FAIL /{printf "%s%s", sep, $2; sep=","}')"
[[ -z "$fails" ]] || nomerge "gates 赤 ($fails)"

# ---------- レビュー（review.md の 1 行目は `# レビュー: PASS` か `# レビュー: FAIL`。kit/roles/reviewer.md）
if [[ "$HAS_REVIEW" == "1" ]]; then
  head1="$(sb "head -1 $WORK/review.md 2>/dev/null || true" | tr -d '\r' | sed 's/[[:space:]]*$//')"
  [[ "$head1" == "# レビュー: PASS" ]] || nomerge "review が PASS でない (${head1:-review.md が無い})"
fi

# ---------- PR そのもの（開いている・draft でない・宛先と head が この run のもの）
v="$(ghq "pr view $num --json state,isDraft,baseRefName,headRefName -q '\"\\(.state) \\(.isDraft) \\(.baseRefName) \\(.headRefName)\"'" || true)"
read -r state draft bref href <<<"$(printf '%s\n' "$v" | tail -1)"
[[ "$state" == "OPEN" ]] || nomerge "PR #$num が OPEN でない (${state:-状態を読めない})"
[[ "$draft" != "true" ]] || nomerge "PR #$num が draft（ゲートに FAIL があったときの印）"
[[ "$bref" == "$BASE" ]] || nomerge "PR #$num の宛先が $BASE でない ($bref)"
[[ "$href" == "$BRANCH" ]] || nomerge "PR #$num の head が $BRANCH でない ($href)"

# ---------- CI（checks が全部 pass になるまで待つ。fail が 1 本でも出たら待たずに止める）
deadline=$(( $(date +%s) + ${AUTOMERGE_WAIT_S:-$((WAIT_MIN * 60))} ))
started=$(date +%s)
checks_n=0
while :; do
  # fail や pending が残ると gh pr checks は非 0 で終わるので、終了コードではなく中身で判断する
  out="$(ghq "pr checks $num --json name,state,bucket -q '.[] | \"\\(.bucket) \\(.name)\"' 2>/dev/null" || true)"
  bad=""; pending=""; checks_n=0
  while read -r bucket name; do
    if [[ -z "$bucket" ]]; then continue; fi
    checks_n=$((checks_n + 1))
    case "$bucket" in
      pass|skipping) ;;
      fail|cancel) bad="${bad:+$bad,}$name" ;;
      *) pending="${pending:+$pending,}$name" ;;    # pending と、gh の版で増えた知らない値は「まだ終わっていない」に寄せる
    esac
  done <<<"$out"
  [[ -z "$bad" ]] || nomerge "CI 赤 ($bad)"
  if [[ $checks_n -eq 0 ]]; then
    # checks が 0 本: 走り始める前かもしれないので少しだけ待ち、それでも 0 本なら PJ の設定に従う
    if [[ $(( $(date +%s) - started )) -ge $GRACE_S ]]; then
      if [[ "$REQUIRE_CHECKS" == "1" ]]; then nomerge "checks が 0 本（CI が動いていない）"; fi
      break
    fi
  elif [[ -z "$pending" ]]; then
    break
  fi
  if [[ $(date +%s) -ge $deadline ]]; then nomerge "checks が $WAIT_MIN 分で終わらない (${pending:-0 本のまま})"; fi
  sleep "$POLL_S"
done

# ---------- GitHub 側のマージ可否（pr-merge.sh と同じ待ち方）
mergeable=""
for _ in $(seq 1 12); do
  mergeable="$(ghq "pr view $num --json mergeable -q .mergeable" 2>/dev/null | tail -1 | tr -d '\r' || true)"
  if [[ "$mergeable" == "MERGEABLE" ]]; then break; fi
  sleep "$MERGEABLE_POLL_S"
done
[[ "$mergeable" == "MERGEABLE" ]] || nomerge "PR #$num が MERGEABLE でない (${mergeable:-判定を読めない})"

# ---------- マージ（--delete-branch は使わない。VM の checkout を切り替えようとして失敗しうる）
if ! merge_out="$(ghq "pr merge $num --$METHOD" 2>&1)"; then
  nomerge "gh pr merge が失敗: $(printf '%s\n' "$merge_out" | tr -d '\r' | grep . | tail -1)"
fi
after="$(ghq "pr view $num --json state,url -q '\"\\(.state) \\(.url)\"'" || true)"
read -r state2 url2 <<<"$(printf '%s\n' "$after" | tail -1)"
[[ "$state2" == "MERGED" ]] || nomerge "gh pr merge の後も PR #$num が MERGED になっていない (${state2:-状態を読めない})"
# マージコミットの sha は GitHub 側の反映が少し遅れて null で返ることがある。数回だけ待ち、取れなくても記録は残す
sha=""
for _ in $(seq 1 3); do
  sha="$(ghq "pr view $num --json mergeCommit -q '.mergeCommit.oid'" 2>/dev/null | tail -1 | tr -d '\r' || true)"
  if [[ "$sha" == "null" ]]; then sha=""; fi
  if [[ -n "$sha" ]]; then break; fi
  sleep "$MERGEABLE_POLL_S"
done

at="$(date -Iseconds)"
sb "cat > $WORK/merged.json" <<EOF
{"sha": "$sha", "method": "$METHOD", "pr_url": "$url2", "base": "$BASE", "at": "$at"}
EOF

if [[ "$DELETE_BRANCH" == "1" ]]; then
  sb "cd \$SANDBOX_APP_DIR && git push -q origin --delete $BRANCH" || echo "[automerge] origin/$BRANCH を消せなかった（人が消すこと）"
fi
sb "cd \$SANDBOX_APP_DIR && gh pr comment $num --body \"aifactory が自動マージした（run ${RUN_NAME:-?} / gates 緑・review $([[ "$HAS_REVIEW" == "1" ]] && echo PASS || echo なし)・checks $checks_n 本 pass・base $BASE・$METHOD）\"" >/dev/null \
  || echo "[automerge] PR にコメントを付けられなかった（マージ自体は済んでいる）"
echo "MERGED: $sha $url2"
