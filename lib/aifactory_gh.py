"""lib/aifactory_gh.py: 制御系（VM の外）で `gh` を使うための共有ヘルパー。

トークンの払い出し（GitHub App。ADR-0008 / ADR-0030）と PR の状態問い合わせを 1 か所に置く。
workflow/bin/run（merge-pr の start: pr）と kanban/bin/kb（sync の PR マージ検知。チケット 345）が使う。

- 秘密（トークン）の値はログにも戻り値にも出さない。呼ぶ側に返すのは「使えなかった理由」だけ
- 失敗は例外にせず理由の文字列で返す。呼ぶ側が「致命」（runner）か「黙って飛ばす」（kb sync）かを決める
"""
import json, os, re, subprocess

SECRET_LINE = re.compile(r"(TOKEN|SECRET|PASSWORD|_KEY)\s*=|gh[pousr]_[A-Za-z0-9]{20,}|sk-ant-", re.I)


def scrub(text):
    """秘密が混ざりうる行を落とす（子プロセスの stderr をそのまま記録・表示するため）"""
    return "\n".join(l for l in text.splitlines() if not SECRET_LINE.search(l))


def ensure_gh_token(pj, repo="", log=None):
    """`gh` を使う前に GH_TOKEN を用意する。空なら GitHub App（ADR-0008）から PJ のリポジトリ限定の
    1 時間トークンを払い出して自分の env に入れる（子プロセスの gh も継承する）。
    戻り: 払い出せなかった理由（既にある / 払い出せたときは None）。トークンの値はログにも記録にも出さない（チケット 249）"""
    if os.environ.get("GH_TOKEN"): return None
    try:
        r = subprocess.run(["sandbox", "gh-app", "token", pj], text=True, capture_output=True, errors="replace")
    except OSError as e:      # sandbox が PATH に無い環境（この VM / CI）。落とさず理由にする
        return scrub(str(e))[-500:]
    token = ([l.strip() for l in (r.stdout or "").splitlines() if l.strip()] or [""])[-1]
    if r.returncode != 0 or not token:
        return (scrub(r.stderr or "").strip() or f"rc={r.returncode}")[-500:]
    os.environ["GH_TOKEN"] = token
    if log: log(f"GH_TOKEN: GitHub App から払い出し（{repo or pj}）")
    return None


def pr_state(pr, repo, timeout=20):
    """PR の今の状態を GitHub に聞く。戻り: (dict, None) か (None, 理由)。
    dict は `gh pr view --json state,mergedAt,closedAt,mergeCommit` の生の中身（時刻は GitHub の Z 付きのまま）"""
    cmd = ["gh", "pr", "view", str(pr), "-R", repo, "--json", "state,mergedAt,closedAt,mergeCommit"]
    try:
        r = subprocess.run(cmd, text=True, capture_output=True, errors="replace", timeout=timeout)
    except OSError as e:      # gh が PATH に無い
        return None, scrub(str(e))[-300:]
    except subprocess.TimeoutExpired:
        return None, f"{timeout}s で応答が無い"
    if r.returncode != 0:
        return None, ([l.strip() for l in scrub(r.stderr or "").splitlines() if l.strip()] or [f"rc={r.returncode}"])[-1][:300]
    try:
        info = json.loads(r.stdout)
    except ValueError as e:
        return None, f"gh の出力を読めない: {e}"
    if not isinstance(info, dict): return None, "gh の出力が JSON のオブジェクトでない"
    return info, None
