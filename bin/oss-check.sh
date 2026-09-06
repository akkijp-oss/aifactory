#!/usr/bin/env bash
# bin/oss-check.sh: 公開前チェック。git 追跡ファイルに、環境固有の名前・私有 PJ・秘密情報らしきものが残っていないかを機械的に見る。
#   bin/oss-check.sh            # 問題があれば非 0
# 許可リスト（例として残してよいもの）は下の ALLOW。追加するときは理由をコメントに書く
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
rc=0

echo "== 1. 追跡されてはいけない置き場"
for p in workspace kanban/kanban.db kanban/tickets kanban/BOARD.md workflow/runs glue/intake.log glue/dispatch.log workflow/prompts docs/infra docs/source/media console/jobs; do
  if git ls-files --error-unmatch -- "$p" >/dev/null 2>&1; then echo "NG  $p が追跡されている（bin/migrate-workspace.sh）"; rc=1; fi
done
if git ls-files 'sandbox/templates/*/provision.sh' | grep -q .; then echo "NG  sandbox/templates/<pj>/ に PJ 定義が残っている"; git ls-files 'sandbox/templates/*/provision.sh'; rc=1; fi
git ls-files 'docs/source/*.transcript.md' | grep -q . && { echo "NG  文字起こしが追跡されている"; rc=1; }
(( rc )) || echo "ok"

echo "== 2. 秘密情報らしきもの（全履歴）"
# パターンは自分自身（このスクリプトの本文）に一致しないように分割して組み立てる
SECRET_PAT="ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|tskey-[A-Za-z0-9-]{10,}|PVEAPIToken""=[^ ]+|BEGIN (RSA|OPENSSH|EC) PRIVATE"" KEY"
secrets="$(git log -p --all | grep -nE "$SECRET_PAT" | head -5)"
if [[ -n "$secrets" ]]; then echo "$secrets" | cut -c1-160; echo "NG  上の行を確認"; rc=1; else echo "ok"; fi

echo "== 3. 環境固有・私有の名前（追跡ファイル）"
# ALLOW: kumitate / akkijp/kumitate は公開許可済みのサンプル、akkijp/aifactory はこのリポジトリ、10.77.x は文書上の既定例、100.64.0.0/10 は Tailscale の CGNAT 範囲（固有情報ではない）
PAT='秋月|akki-pve|a1pve|a1mpve|pvexf|hokenss|kosuke19952000|marugoto|devboard|pcbcad|companyhub|granthub|zenkoku|192\.168\.[0-9]+\.[0-9]+|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]+\.[0-9]+|iDRAC|homelab|mytask|オーナー指示'
hits="$(git ls-files -z | grep -zv '^bin/oss-check.sh$' | xargs -0 grep -nE "$PAT" 2>/dev/null | grep -vE '100\.64\.0\.0/10|192\.168\.0\.0/16' )"
if [[ -n "$hits" ]]; then echo "$hits" | head -60; echo "NG  $(echo "$hits" | wc -l | tr -d ' ') 件"; rc=1; else echo "ok"; fi

echo "== 4. 必須ファイル"
for f in LICENSE README.md README.ja.md CONTRIBUTING.md SECURITY.md CODE_OF_CONDUCT.md CHANGELOG.md .github/workflows/ci.yml; do
  [[ -f "$f" ]] && echo "ok  $f" || { echo "NG  $f が無い"; rc=1; }
done
exit $rc
