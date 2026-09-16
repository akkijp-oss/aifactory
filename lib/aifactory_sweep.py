"""lib/aifactory_sweep.py: 掃き寄せ（`sandbox: uncommitted changes by agent`）で拾わないものの規則。判定はここ 1 か所（チケット 572）。

掃き寄せは **救済**（step が時間上限・利用枠で切られたとき、未コミットの実装を次の attempt へ渡す）であって、
「成果物に何を載せるか」の判断ではない。意図した変更は実装役が自分でコミットする（役割の約束にそう書いてある）。
だから **意図の確認が取れない依存ファイル**（lock / manifest）は拾わず、HEAD の内容へ戻す。

- 規則の正本はここ。`workflow/bin/run`（Linux/proxmox）と `workflow/lib/windows.py`（PowerShell）は
  この lib の返す pathspec と文面を使うだけで、自分で glob も文言も持たない（写しを 2 か所に置かない。#578）
- 既定の除外はチケット 572 が名指しした 2 つだけ（`**/package.json` / `**/pnpm-lock.yaml`）。
  npm / yarn / uv の lock を足すかは、**過去の run が実際に何を拾ってきたかを集計してから**決める（推測で増やさない）
- PJ ごとに変えたいときは project.yml の `sweep_exclude`（glob の配列）。`[]` と書けば除外なし（＝従来どおり全部拾う）
- 除外は「追跡済み / 未追跡」では表せない（`git add -u` は既に未追跡を外している）。パス指定でしか実現できない

pathspec の形について:
- `:/`（top magic）が要る。app_dir がリポジトリ直下でない PJ（kumitate は `apps/kumitate`）で `git add -u` に
  パスを付けると **cwd 相対**になり、掃き寄せの範囲が黙って縮む。`:/` を先頭に置いてツリー全体を対象にする
- 除外は `:(top,glob,exclude)<glob>`。`glob` を付けないと `**` が効かない

「除外したファイル」を **glob の判定でだけ決める**こと（「staged にならなかったもの」で代用しない）:
- 代用すると `git add` が失敗した回（step が時間上限で殺され `.git/index.lock` が残った等）に staged が空になり、
  **汚れている全ファイル**が「除外」と見なされて `git restore --worktree` で消える。救済の場でこそ起きる
- 判定は git 自身にさせる（`add_pathspecs()` を `git status` にも渡し、残った一覧＝拾う対象とする）。
  fnmatch で `**` を再実装しない（git の glob と挙動がずれる）

ファイル名の列挙は **必ず `-z` と `core.quotePath=false`** で取る（`STATUS_CMD` / `STAGED_CMD`）:
- 既定の `git status --porcelain` は非 ASCII を `"\346\227\245..."` と八進で括る。`git diff --cached --name-only` も同じ。
  中途半端に外すと同じファイルが別の文字列になり、記録が嘘をつく（拾ったのに「除外して戻した」と書く）
"""

DEFAULT_EXCLUDE = ["**/package.json", "**/pnpm-lock.yaml"]

COMMIT_BODY_HEAD = "掃き寄せ（step の終わりに残っていた未コミットの変更を救済）"

# ファイル名の列挙はこの 2 つだけを使う（`-z` + quotePath=false。八進エスケープと引用符を混ぜない）。
# `STATUS_CMD` は末尾に ` -- <pathspec>` を足して「除外を効かせた一覧」も取れる
STATUS_CMD = "git -c core.quotePath=false status --porcelain -z --untracked-files=no"
STAGED_CMD = "git -c core.quotePath=false diff --cached --name-only -z"

# `git add` が通ったことを stdout で確かめるための目印（sb は返り値を捨てて stdout しか返さない）
ADD_OK = "SWEEP-ADD-OK"


def excludes(project):
    """project.yml の dict → 掃き寄せで拾わない glob の一覧。

    `sweep_exclude` が list ならそれ（`[]` は「除外なし」という明示なので既定に戻さない）、無ければ既定。
    """
    if isinstance(project, dict):
        v = project.get("sweep_exclude")
        if isinstance(v, list): return [str(x) for x in v]
    return list(DEFAULT_EXCLUDE)


def add_pathspecs(excl):
    """`git add -u -- <これ>` に渡す pathspec。先頭の `:/` は必須（cwd が subdir でもツリー全体を見る）"""
    return [":/"] + [f":(top,glob,exclude){p}" for p in excl]


def restore_pathspecs(paths):
    """`git restore --source=HEAD --staged --worktree -- <これ>` に渡す pathspec。

    glob ではなく **実際に除外した実パス**を渡す（リポジトリ直下からの相対）。glob を渡すと、
    1 つも一致しない PJ で `pathspec did not match` になり、戻す必要が無い回まで失敗する。
    """
    return [f":(top){p}" for p in paths]


def status_paths(porcelain_z):
    """`STATUS_CMD`（`git status --porcelain -z`）の出力 → パスの一覧（リポジトリ直下からの相対）。

    `-z` は 1 レコードが `XY SP <path> NUL`。rename / copy だけは元のパスが次の NUL 区切りに続くので読み飛ばし、
    新しい方を採る。引用符も八進エスケープも出ない（`STATUS_CMD` が `core.quotePath=false` を渡している）。
    """
    fields = (porcelain_z or "").split("\0")
    out, i = [], 0
    while i < len(fields):
        rec = fields[i]; i += 1
        if len(rec) < 4: continue
        if "R" in rec[:2] or "C" in rec[:2]: i += 1     # 続く 1 フィールドは rename / copy の元のパス
        out.append(rec[3:])
    return out


def nul_paths(out):
    """`STAGED_CMD`（`--name-only -z`）の出力 → パスの一覧"""
    return [p for p in (out or "").split("\0") if p]


def excluded_paths(dirty, kept, pre_staged):
    """（掃き寄せ前の未コミット一覧, 除外 pathspec を付けて数え直した一覧, 掃き寄せ前から staged な一覧）
    → 除外して HEAD へ戻すファイル。

    ★判定は **除外の glob（git が `kept` として返した差）だけ**で決める。「`git add` の後で staged にならなかったもの」
    で代用してはいけない: add が失敗した回（`.git/index.lock` が残った等）に staged が空になり、実装役の作業を
    全部「除外」と見なして worktree ごと消す。救済（`wip: step timeout`）の場でこそ起きる事故。

    実装役が自分で `git add` 済みのファイル（`pre_staged`）は、依存ファイルでも戻さない（意図が明示されている。
    票の「依存の変更は明示的に要求されたときだけ」）。
    """
    kept = set(kept); pre = set(pre_staged)
    return [p for p in dict.fromkeys(dirty) if p not in kept and p not in pre]


def commit_body(committed, excluded):
    """掃き寄せコミットの本文（本文ゼロをやめる。差分を開かずに「何を・なぜ」が読める）"""
    lines = [COMMIT_BODY_HEAD, ""]
    lines.append("拾ったファイル:")
    lines += [f"- {p}" for p in committed] or ["- （無し）"]
    if excluded:
        lines += ["", "除外して HEAD の内容へ戻したファイル（依存ファイルは意図の確認が取れないので掃き寄せない。チケット 572）:"]
        lines += [f"- {p}" for p in excluded]
    return "\n".join(lines) + "\n"


def commit_body_rule_only(excl):
    """拾った一覧を取り回せない実装（`workflow/lib/windows.py` の PowerShell 版）向けの本文。

    一覧は書けないが「何を除外したか」の規則は書ける。本文ゼロよりは差分を開く前に読める。
    """
    lines = [COMMIT_BODY_HEAD, "",
             "拾ったファイルの一覧はこのコミットの差分（git show --stat）を見ること（Windows 版は一覧を取らない）。", ""]
    if excl:
        lines += ["次の glob に一致するファイルは掃き寄せの対象から外してある（依存ファイルは意図の確認が取れないため。チケット 572）:"]
        lines += [f"- {p}" for p in excl]
    return "\n".join(lines) + "\n"


def summary(message, committed, excluded):
    """run の stdout / ログに出す 1 件分の要約（拾ったこと自体に気づける形にする）"""
    parts = [f"[run] 掃き寄せ（{message}）: 拾った {len(committed)} 件 / 除外して戻した {len(excluded)} 件"]
    if committed: parts.append("  拾った: " + ", ".join(committed))
    if excluded: parts.append("  除外して戻した: " + ", ".join(excluded))
    return "\n".join(parts)


def add_failed_summary(message, dirty):
    """`git add` が失敗した回の要約。**この回は何も戻さず・何もコミットしない**（未コミットの実装は作業ツリーに残る）。
    黙って落とすと、救済されなかったことに誰も気づけない"""
    return (f"[run] 掃き寄せ（{message}）: git add が失敗したので何もしなかった（戻しもコミットもしない）。"
            f"未コミットのまま残っている {len(dirty)} 件: " + ", ".join(dirty[:20]))


def dirty_summary(stage, restored):
    """run 開始時（checkout / prepare の直後）に作業ツリーが汚れていたときの 1 件分の要約"""
    return (f"[run] 開始時の作業ツリーが汚れていた（{stage}）: {len(restored)} 件を HEAD の内容へ戻した\n"
            + "  " + ", ".join(restored))
