"""lib/aifactory_attachments.py: チケットの添付（画像・PDF・CSV など）の正本。判定はここに 1 か所（ADR-0015 と同じ考え）。

kb / workflow/bin/run / console/lib/core.py が全部ここを呼ぶ。置き場は lib/aifactory_paths.py の ATTACHMENTS（ADR-0016）。

  <ATTACHMENTS>/<チケット id>/<sanitize 済みのファイル名>

- 本文（tickets/<id>-<pj>-<slug>.md）には添付のことを書かない。正本は実体のファイルで、一覧は表示側が導く
- 上限: 1 ファイル 20 MiB / チケット合計 100 MiB。超えたら ValueError（メッセージは日本語）
- ファイル名は sanitize する（パス区切り・`..`・制御文字を落とす）。同じ名前が既にあれば拡張子の前に `-2`, `-3` … を付ける
- workspace は git 追跡外＝ bin/oss-check.sh の検査対象外。秘密情報（トークン・鍵）は添付しない
- 標準ライブラリだけで動く（VM の中でも ctl でも同じように使える）
"""
import datetime, mimetypes, pathlib, re, shutil, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); import aifactory_paths as paths

MAX_FILE = 20 * 1024 * 1024        # 1 ファイルの上限
MAX_TOTAL = 100 * 1024 * 1024      # チケット 1 件の合計の上限
MAX_NAME_BYTES = 255               # ext4 / APFS のファイル名の上限
DEFAULT_TYPE = "application/octet-stream"
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def dir_for(tid):
    """チケット <tid> の添付ディレクトリ（作らない）。ATTACHMENTS はモジュール属性として毎回引く（テストが差し替える）。

    id は数字（`0101` は `101` に揃える）。runner は CLI から任意の task-id を受け取れるので、
    数字でない値でも落とさず sanitize した名前にする（置き場の外には出さない）"""
    s = sanitize(tid)
    return paths.ATTACHMENTS / (str(int(s)) if s.isdigit() else s)


def sanitize(name):
    """人や LLM が渡した名前を、置き場の中だけで完結する 1 つのファイル名に落とす。

    制御文字を除く → `/` `\\` で分けた最後の要素 → 前後の空白を除く → `` / `.` / `..` は `file` → 先頭の `.` は `_` → 255 バイトに切る
    """
    s = CONTROL.sub("", str(name)).replace("\\", "/").split("/")[-1].strip()
    if s in ("", ".", ".."): s = "file"
    if s.startswith("."): s = "_" + s[1:]
    b = s.encode("utf-8")[:MAX_NAME_BYTES]
    while b:      # 切った所が UTF-8 の途中なら 1 バイトずつ戻す
        try: return b.decode("utf-8")
        except UnicodeDecodeError: b = b[:-1]
    return "file"


def _unique(d, name):
    """同じ名前が既にあれば拡張子の前に `-2`, `-3` … を付ける（上書きしない）"""
    if not (d / name).exists(): return name
    stem, dot, ext = name.rpartition(".")
    if not dot: stem, ext = name, ""
    n = 2
    while True:
        cand = f"{stem}-{n}" + (f".{ext}" if dot else "")
        if not (d / cand).exists(): return cand
        n += 1


def total_size(tid):
    """チケットの添付の合計バイト数"""
    d = dir_for(tid)
    if not d.is_dir(): return 0
    return sum(f.stat().st_size for f in d.iterdir() if f.is_file())


def guess_type(name):
    return mimetypes.guess_type(name)[0] or DEFAULT_TYPE


def add(tid, src, name=None):
    """src（ファイル）をチケット <tid> の添付として複製し、保存した名前を返す。上限を超えたら ValueError"""
    src = pathlib.Path(src)
    if not src.is_file(): raise ValueError(f"通常ファイルではない: {src}")
    size = src.stat().st_size
    if size > MAX_FILE: raise ValueError(f"1 ファイルの上限 {MAX_FILE // 1024 // 1024} MiB を超えている: {src.name}（{human(size)}）")
    cur = total_size(tid)
    if cur + size > MAX_TOTAL:
        raise ValueError(f"チケット {tid} の添付の合計が上限 {MAX_TOTAL // 1024 // 1024} MiB を超える"
                         f"（今 {human(cur)} + {human(size)}）。要らない添付を kb detach で消すこと")
    d = dir_for(tid); d.mkdir(parents=True, exist_ok=True)
    saved = _unique(d, sanitize(name or src.name))
    shutil.copyfile(src, d / saved)
    return saved


def add_bytes(tid, name, data):
    """中身をバイト列で受け取って添付する（MCP の base64・console の multipart 用）。保存した名前を返す"""
    size = len(data)
    if size > MAX_FILE: raise ValueError(f"1 ファイルの上限 {MAX_FILE // 1024 // 1024} MiB を超えている: {name}（{human(size)}）")
    cur = total_size(tid)
    if cur + size > MAX_TOTAL:
        raise ValueError(f"チケット {tid} の添付の合計が上限 {MAX_TOTAL // 1024 // 1024} MiB を超える"
                         f"（今 {human(cur)} + {human(size)}）。要らない添付を kb detach で消すこと")
    d = dir_for(tid); d.mkdir(parents=True, exist_ok=True)
    saved = _unique(d, sanitize(name))
    (d / saved).write_bytes(data)
    return saved


def path_of(tid, name):
    """添付 1 件のパス。sanitize して名前が変わる（＝パスを渡された）なら None（置き場の外へ出さない）"""
    s = sanitize(name)
    if s != str(name): return None
    p = dir_for(tid) / s
    return p if p.is_file() else None


def remove(tid, name):
    """添付を 1 件消す。消したら True、無ければ False"""
    p = path_of(tid, name)
    if not p: return False
    p.unlink()
    d = dir_for(tid)
    if not any(d.iterdir()): d.rmdir()      # 空になったディレクトリは残さない
    return True


def listing(tid):
    """添付の一覧 [{name, size, type, added}]（名前順）。添付が無ければ空リスト"""
    d = dir_for(tid)
    if not d.is_dir(): return []
    out = []
    for f in sorted(d.iterdir()):
        if not f.is_file() or f.is_symlink(): continue
        st = f.stat()
        out.append({"name": f.name, "size": st.st_size, "type": guess_type(f.name),
                    "added": datetime.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds")})
    return out


def human(n):
    """人が読むサイズ（一覧と失敗のメッセージ用）"""
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB": return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


if __name__ == "__main__":
    import json
    print(json.dumps(listing(sys.argv[1]), ensure_ascii=False, indent=2))
