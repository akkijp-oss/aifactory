#!/usr/bin/env bash
# kit/steps/gates.sh: PJ のゲート（templates/<pj>/<gates>）を VM で走らせ、結果を ~/work/<task>/gates.txt に置く。全緑なら 0
#
# 赤が出たら、その赤いゲートだけを base（origin/$BASE）でも走らせ直し、base でも赤いものは FAIL → INFO に落とす（チケット 330）。
# 「自分の変更が壊したのか、元から赤いのか」を実装役の推測ではなく機械で切り分けるため。base でも赤かった名前は
# $WORK/base-red.txt に置き、runner が run の記録（known_red_gates）に足す。
#
# 赤いゲートの VM 内ログ（~/gates/<name>.log）は $WORK/gates/<name>.log に抜粋を残す（チケット 331）。VM は run の終わりに
# 返却・初期化されるので、そこに置かないと「どのテストがどう落ちたか」は run が終わった時点で誰にも読めなくなる。
#
# base 確認は同じゲート名で走るので、出力先を分けないと診断のための再実行が診断対象（本実行の赤いログ）を消す。
# PJ の gates.sh に env GATES_LOG_SUFFIX=.base を渡し、base 側は ~/gates/<name>.base.log に書かせる（チケット 551）。
# runner から次の env で呼ばれる: PJ TASK RUN_DIR PROJECT_DIR GATES WORK BASE BRANCH KNOWN_RED REPO
set -uo pipefail
: "${TASK:?}" "${PROJECT_DIR:?}" "${GATES:?}" "${WORK:?}"
# ログの転記は秘密の形（sk-ant-… / gh*_ / KEY=値）を伏せてから run に置く（scrub.sh。PR 本文にも同じものが通る）
SCRUB="$(dirname "${BASH_SOURCE[0]}")/scrub.sh"
GATES_LOG_LINES="${GATES_LOG_LINES:-300}"           # work/gates/<name>.log に残す末尾の行数
GATES_LOG_MAX_BYTES="${GATES_LOG_MAX_BYTES:-204800}"  # 1 ゲートあたりの上限。超える分は末尾を優先して切る
GATES_LOG_RE='✗|×|FAIL|Error|error:|Traceback'      # 抜粋に拾うエラーらしい行
KEY="$HOME/.ssh/conf.d/aifactory/sb_ed25519"
ip="$(jq -r --arg id "$TASK" '.[$id].ip' "$HOME/.config/sandbox/state.json")"
scp -q -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$PROJECT_DIR/$GATES" "dev@$ip:/home/dev/gates.sh"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

# ---------- 配った PJ 定義が origin/$BASE の版か（チケット 487）
# 配るのは制御系（ctl）の作業ツリー、正本は origin/$BASE。ctl は main 追従（ADR-0042）なので、base が develop の
# PJ では develop に着地した PJ 定義が昇格まで配られない。#446 で足した unittest-pull は「行そのものが無い」まま
# 全緑の run が 2 回出た（赤くならないので誰も気づかない）。黙って減らないよう、差があれば 1 行言う。
# FAIL にはしない: 配布経路の問題であって実装役の変更ではなく、FAIL にすると全 run が implement へ戻る（ADR-0042 §4）。
# git が無い・PJ 定義が追跡外（workspace/projects）・repo が違う PJ では何も言わない（比べる正本が無い＝黙る）
pj_drift() {
  local base="${BASE:-main}" url rel changed short bits="" missing gates_note
  [ -n "${REPO:-}" ] || return 0
  url="$(git -C "$PROJECT_DIR" remote get-url origin 2>/dev/null)" || return 0
  case "$url" in *"$REPO"*) ;; *) return 0 ;; esac                     # 別リポジトリの PJ 定義は比べない
  git -C "$PROJECT_DIR" rev-parse -q --verify "refs/remotes/origin/$base" >/dev/null 2>&1 || return 0
  rel="$(git -C "$PROJECT_DIR" rev-parse --show-prefix 2>/dev/null)"; [ -n "$rel" ] || return 0
  changed="$(git -C "$PROJECT_DIR" diff --name-only "origin/$base" -- . 2>/dev/null)"
  [ -n "$changed" ] || return 0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    short="${f#"$rel"}"; gates_note=""
    if [ "$short" = "$GATES" ]; then     # ゲートは名前まで出す（「どのゲートが走っていないか」が本題）
      git -C "$PROJECT_DIR" show "origin/$base:$f" 2>/dev/null | awk '/^gate /{print $2}' | sort -u > "$tmp/gates-base.txt"
      awk '/^gate /{print $2}' "$PROJECT_DIR/$GATES" 2>/dev/null | sort -u > "$tmp/gates-here.txt"
      missing="$(comm -23 "$tmp/gates-base.txt" "$tmp/gates-here.txt" 2>/dev/null | tr '\n' ' ')"
      missing="${missing% }"
      [ -n "$missing" ] && gates_note=" (missing: $missing)"
    fi
    bits="$bits $short$gates_note"
  done <<< "$changed"
  [ -n "$bits" ] || return 0
  printf 'INFO pj-drift %s differs from origin/%s:%s' "$rel" "$base" "$bits"
}
drift="$(pj_drift 2>/dev/null)"

# PJ の gates.sh に base を渡す（`bin/changelog-release check --base origin/$BASE` のように base と比べるゲートがある。
# 渡さないと既定の main と比べ、VM の origin/main はテンプレート時点で古いので、develop 向きの PJ では毎回赤になった。2026-09-10 run 347）
out="$(sandbox ssh "$TASK" "BASE=$(printf '%q' "${BASE:-main}") bash ~/gates.sh")"
# base で既に赤いゲート（project.yml の known_red_gates、env KNOWN_RED に空白区切り）は INFO に格下げする
for k in ${KNOWN_RED:-}; do out="$(sed -E "s/^FAIL $k( |$)/INFO $k red (known on base; not a gate)\1/" <<< "$out")"; done
fails="$(awk '/^FAIL/{print $2}' <<< "$out")"

# ---------- base でも赤か（赤が出たときだけ、赤いゲートだけ）
base_block=""
# 前の回の結果を残さない。base を見なかった回は空にする（--resume で回し直したとき、古い !restore-failed を
# runner が読み直して再び人間へ回してしまうため）。gates/ も毎回作り直す（前の回に赤かったゲートが緑になったのに
# 古いログが残っていると、reviewer と人が「まだ赤い」と読む）
sandbox ssh "$TASK" "mkdir -p $WORK && : > $WORK/base-red.txt && rm -rf $WORK/gates && mkdir -p $WORK/gates"

# ---------- 赤いゲートのログを work/ に残す（チケット 331）
# base 側は ~/gates/<name>.base.log に書くので本実行のログは残る（#551）が、取るのはここ（base 確認の前）のままにする。
# INFO に落とした分（known_red / この後 base でも赤と分かる分）もファイルは残す。「元から赤い」の根拠を reviewer と
# 人が run の記録だけで確かめられるようにするため。実装役へ戻す依頼文に載せるかどうかは runner が FAIL だけで選ぶ
red_head="$(awk '/^(FAIL|INFO) /{print $2}' <<< "$out")"
for g in $red_head; do
  q="$(printf '%q' "$g")"
  sandbox ssh "$TASK" "tail -c $GATES_LOG_MAX_BYTES ~/gates/$q.log" > "$tmp/raw-$g.log" 2>/dev/null
  [ -s "$tmp/raw-$g.log" ] || continue
  { printf '# %s.log on HEAD (%s), fetched %s bytes / %s lines from ~/gates/%s.log, kept tail %s lines\n' \
      "$g" "${BRANCH:-?}" "$(wc -c < "$tmp/raw-$g.log")" "$(wc -l < "$tmp/raw-$g.log")" "$g" "$GATES_LOG_LINES"
    printf '\n=== excerpt (%s)\n' "$GATES_LOG_RE"
    LC_ALL=C.UTF-8 grep -anE "$GATES_LOG_RE" "$tmp/raw-$g.log" | tail -100
    printf '\n=== tail %s\n' "$GATES_LOG_LINES"
    tail -n "$GATES_LOG_LINES" "$tmp/raw-$g.log"
  } > "$tmp/excerpt-$g.log"
  if [ "$(wc -c < "$tmp/excerpt-$g.log")" -gt "$GATES_LOG_MAX_BYTES" ]; then
    # 上限は失敗にせず切り捨てる。切ったという事実だけ 1 行残す（ADR-0034 と同じ扱い）
    keep=$(( GATES_LOG_MAX_BYTES - ${#g} - 60 )); [ "$keep" -lt 1024 ] && keep=1024
    { printf '[truncated: kept last %s bytes of ~/gates/%s.log]\n' "$keep" "$g"
      tail -c "$keep" "$tmp/excerpt-$g.log"; } > "$tmp/cut-$g.log"
    mv "$tmp/cut-$g.log" "$tmp/excerpt-$g.log"
  fi
  bash "$SCRUB" < "$tmp/excerpt-$g.log" | sandbox ssh "$TASK" "cat > $WORK/gates/$q.log"
done

if [ -n "$fails" ] && [ -n "${BASE:-}" ] && [ -n "${BRANCH:-}" ]; then
  # `=== <name>.log (tail 60)` 節の元。base 側は別ファイル（.base.log）に書かせるので上書きはされない（#551）
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
echo "BASE-CHECK origin/$base $sha (logs: ~/gates/<name>.base.log)"
# 本実行の ~/gates/<name>.log を潰さないよう、base 側は別名前空間に書かせる（チケット 551）。export はしない
BASE="$base" GATES_LOG_SUFFIX=.base bash "$HOME/gates.sh" "$@"
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
  # PJ の gates.sh が GATES_LOG_SUFFIX を見ていないと、base の結果が本実行の ~/gates/<name>.log を上書きしたままになる（#551）。
  # 追跡外の私有 PJ 定義はこの repo から直せないので、黙って元の事故に戻らないよう 1 行残す。FAIL/INFO にはしない（判定は不変）
  if ! grep -q '^BASE-CHECK-SKIP' <<< "$base_out"; then
    for g in $fails; do
      q="$(printf '%q' "$g")"
      [ "$(sandbox ssh "$TASK" "test -f ~/gates/$q.base.log && echo yes")" = yes ] && continue
      base_block="$base_block$(printf '\nBASE-CHECK-LOG-MISSING %s (PJ の gates.sh が GATES_LOG_SUFFIX を見ていない。~/gates/%s.log は base の結果で上書きされたかもしれない)' "$g" "$g")"
    done
  fi
  for g in $base_red; do    # 「base でも赤い」の根拠を reviewer が確かめられるように base 側のログ末尾も残す
    base_block="$base_block$(printf '\n\n--- %s.base.log on base (tail 30)\n%s' "$g" "$(sandbox ssh "$TASK" "tail -30 ~/gates/$g.base.log")")"
  done
fi

# ---------- 結果を組み立てて置く
left="$(awk '/^FAIL/{print $2}' <<< "$out")"
# 配布版と正本のズレは判定の行の先頭に置く（gates.txt の 1 行目・console の INFO・実装役への依頼文で見える）
[ -n "$drift" ] && out="$drift"$'\n'"$out"
{ printf '%s\n' "$out"
  if [ -n "$base_block" ]; then printf '%s\n' "$base_block"; fi
  for g in $left; do printf '\n=== %s.log (tail 60)\n' "$g"; cat "$tmp/head-$g.log" 2>/dev/null; done
} | bash "$SCRUB" > "$tmp/gates.txt"
sandbox ssh "$TASK" "mkdir -p $WORK && cat > $WORK/gates.txt" < "$tmp/gates.txt"
# 実装役に戻す依頼文には標準出力が入る。判定の根拠（base での結果）まで見せる
printf '%s\n' "$out"
if [ -n "$base_block" ]; then printf '%s\n' "$base_block"; fi
[ -z "$left" ]
