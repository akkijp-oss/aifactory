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
"""

DEFAULT_EXCLUDE = ["**/package.json", "**/pnpm-lock.yaml"]

COMMIT_BODY_HEAD = "掃き寄せ（step の終わりに残っていた未コミットの変更を救済）"


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


def status_paths(porcelain):
    """`git status --porcelain --untracked-files=no` の出力 → パスの一覧（リポジトリ直下からの相対）。

    rename（`R  old -> new`）は新しい方を採る。空白を含む名前は git が `"..."` で括るので外す。
    """
    out = []
    for line in (porcelain or "").splitlines():
        if len(line) < 4: continue
        path = line[3:]
        if " -> " in path: path = path.split(" -> ", 1)[1]
        out.append(_unquote(path))
    return out


def _unquote(path):
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        try: return path[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
        except Exception: return path[1:-1]
    return path


def split(dirty, staged):
    """（掃き寄せ前の未コミット一覧, `git add` 後に staged な一覧）→ (拾った, 除外した)。

    staged に載らなかった未コミットのファイルが「除外した」。
    実装役が自分で `git add` 済みのファイルは staged 側に居るので、除外の対象にならない（意図が明示されている）。
    """
    dirty = list(dict.fromkeys(dirty)); staged = list(dict.fromkeys(staged))
    committed = [p for p in dirty if p in staged] + [p for p in staged if p not in dirty]
    excluded = [p for p in dirty if p not in staged]
    return committed, excluded


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


def dirty_summary(stage, restored):
    """run 開始時（checkout / prepare の直後）に作業ツリーが汚れていたときの 1 件分の要約"""
    return (f"[run] 開始時の作業ツリーが汚れていた（{stage}）: {len(restored)} 件を HEAD の内容へ戻した\n"
            + "  " + ", ".join(restored))
