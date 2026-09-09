#!/usr/bin/env bash
# kit/steps/gates.sh: PJ のゲート（templates/<pj>/<gates>）を VM で走らせ、結果を ~/work/<task>/gates.txt に置く。全緑なら 0
#
# 赤が出たら、その赤いゲートだけを base（origin/$BASE）でも走らせ直し、base でも赤いものは FAIL → INFO に落とす（チケット 330）。
# 「自分の変更が壊したのか、元から赤いのか」を実装役の推測ではなく機械で切り分けるため。base でも赤かった名前は
# $WORK/base-red.txt に置き、runner が run の記録（known_red_gates）に足す。
# runner から次の env で呼ばれる: PJ TASK RUN_DIR PROJECT_DIR GATES WORK BASE BRANCH KNOWN_RED
set -uo pipefail
: "${TASK:?}" "${PROJECT_DIR:?}" "${GATES:?}" "${WORK:?}"
KEY="$HOME/.ssh/conf.d/aifactory/sb_ed25519"
ip="$(jq -r --arg id "$TASK" '.[$id].ip' "$HOME/.config/sandbox/state.json")"
scp -q -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$PROJECT_DIR/$GATES" "dev@$ip:/home/dev/gates.sh"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

out="$(sandbox ssh "$TASK" "bash ~/gates.sh")"
# base で既に赤いゲート（project.yml の known_red_gates、env KNOWN_RED に空白区切り）は INFO に格下げする
for k in ${KNOWN_RED:-}; do out="$(sed -E "s/^FAIL $k( |$)/INFO $k red (known on base; not a gate)\1/" <<< "$out")"; done
fails="$(awk '/^FAIL/{print $2}' <<< "$out")"

# ---------- base でも赤か（赤が出たときだけ、赤いゲートだけ）
base_block=""
# 前の回の結果を残さない。base を見なかった回は空にする（--resume で回し直したとき、古い !restore-failed を
# runner が読み直して再び人間へ回してしまうため）
sandbox ssh "$TASK" "mkdir -p $WORK && : > $WORK/base-red.txt"
if [ -n "$fails" ] && [ -n "${BASE:-}" ] && [ -n "${BRANCH:-}" ]; then
  # base で回し直すと ~/gates/<name>.log が base の結果で上書きされる。HEAD 側のログ末尾を先に取っておく
  for g in $fails; do sandbox ssh "$TASK" "tail -60 ~/gates/$g.log" > "$tmp/head-$g.log" 2>/dev/null; done
  sandbox ssh "$TASK" "cat > ~/base-check.sh" <<'BASE_CHECK'
#!/usr/bin/env bash
# ~/base-check.sh <base> <branch> <ゲート名...>: 同じゲートを origin/<base> でも走らせて、その赤が元からのものか確かめる。
# 追跡外の生成物（node_modules / .venv など）をそのまま使いたいので、別 worktree は作らず同じ作業コピーで checkout を差し替える
# （新しい worktree には生成物が無く、ほぼ全ゲートが赤くなって「base でも赤」を誤判定する）。
set -uo pipefail
base=$1; branch=$2; shift 2
cd "${SANDBOX_APP_DIR:?}" 2>/dev/null || { echo "BASE-CHECK-SKIP app dir に入れない"; exit 0; }
root="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "BASE-CHECK-SKIP git 管理下でない"; exit 0; }
cd "$root" || { echo "BASE-CHECK-SKIP $root に入れない"; exit 0; }
git fetch -q origin "$base" 2>/dev/null
sha="$(git rev-parse --short "origin/$base" 2>/dev/null)" || { echo "BASE-CHECK-SKIP origin/$base が無い"; exit 0; }
stashed=
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  git stash push -u -q -m aifactory-base-check 2>/dev/null || { echo "BASE-CHECK-SKIP 未コミットの変更を退避できない"; exit 0; }
  stashed=1
fi
restore() {                     # どこで抜けても作業ブランチへ戻す。戻し切れなければそう言う（runner が人間へ回す）
  local back=0
  # base 側の gate が追跡ファイルを書き換えていると素の checkout は通らない。ここで捨てるのは base 側 gate の副産物だけ
  # （実装役の未コミット分は stash 済み、コミット分は $branch にある）ので -f で戻す。戻せないまま人間へ回すと wip が base になる
  git checkout -q "$branch" 2>/dev/null || git checkout -q -f "$branch" 2>/dev/null || back=1
  [ "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" = "$branch" ] || back=1
  if [ -n "$stashed" ]; then
    git stash pop -q 2>/dev/null || back=1
    git stash list 2>/dev/null | grep -q aifactory-base-check && back=1
  fi
  if [ "$back" = 0 ]; then echo "BASE-RESTORED $branch"; else echo "BASE-RESTORE-FAILED $branch"; fi
}
trap restore EXIT
git checkout -q --detach "origin/$base" 2>/dev/null || { echo "BASE-CHECK-SKIP origin/$base を checkout できない"; exit 0; }
echo "BASE-CHECK origin/$base $sha"
bash "$HOME/gates.sh" "$@"
BASE_CHECK
  args=""; for g in $fails; do args="$args $(printf '%q' "$g")"; done
  base_out="$(sandbox ssh "$TASK" "bash ~/base-check.sh $(printf '%q' "$BASE") $(printf '%q' "$BRANCH")$args")"
  base_red="$(awk '/^FAIL/{print $2}' <<< "$base_out")"
  for k in $base_red; do out="$(sed -E "s/^FAIL $k( |$)/INFO $k red (also red on base; not a gate)\1/" <<< "$out")"; done
  # runner はこれを読んで state.json の known_red_gates に足す（project.yml は書き換えない）
  { printf '%s\n' "$base_red"
    if grep -q '^BASE-RESTORE-FAILED' <<< "$base_out"; then echo '!restore-failed'; fi
  } | sed '/^$/d' | sandbox ssh "$TASK" "mkdir -p $WORK && cat > $WORK/base-red.txt"
  base_block="$(printf '\n=== base check: origin/%s\n%s' "$BASE" "$base_out")"
  for g in $base_red; do    # 「base でも赤い」の根拠を reviewer が確かめられるように base 側のログ末尾も残す
    base_block="$base_block$(printf '\n\n--- %s.log on base (tail 30)\n%s' "$g" "$(sandbox ssh "$TASK" "tail -30 ~/gates/$g.log")")"
  done
fi

# ---------- 結果を組み立てて置く
left="$(awk '/^FAIL/{print $2}' <<< "$out")"
{ printf '%s\n' "$out"
  if [ -n "$base_block" ]; then printf '%s\n' "$base_block"; fi
  for g in $left; do printf '\n=== %s.log (tail 60)\n' "$g"; cat "$tmp/head-$g.log" 2>/dev/null; done
} > "$tmp/gates.txt"
sandbox ssh "$TASK" "mkdir -p $WORK && cat > $WORK/gates.txt" < "$tmp/gates.txt"
# 実装役に戻す依頼文には標準出力が入る。判定の根拠（base での結果）まで見せる
printf '%s\n' "$out"
if [ -n "$base_block" ]; then printf '%s\n' "$base_block"; fi
[ -z "$left" ]
