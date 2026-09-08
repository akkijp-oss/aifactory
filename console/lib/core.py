"""console/lib/core.py: aifactory の読み書きの正本。bin/console（HTTP）と bin/mcp（stdio）が共有する。

- 読む: kanban.db（読み取り専用）/ runs/ / ~/.config/sandbox/state.json / intake・dispatch のログ
- 動かす: kb / intake / dispatch / sandbox を子プロセスで。長いものは JobStore（console/jobs/）。判定（二重起動・入力検査）はここに 1 つ
- 置き場は lib/aifactory_paths.py（AIFACTORY_WORKSPACE。KB_ROOT / CONSOLE_JOBS で個別に差し替え可）
"""
import contextlib, datetime, fcntl, json, os, pathlib, re, signal, sqlite3, subprocess, sys, threading, time

HERE = pathlib.Path(__file__).resolve().parent.parent          # console/（lib/ の親）
REPO = HERE.parent
sys.path.insert(0, str(REPO / "lib")); import aifactory_paths as paths
STATIC = HERE / "static"
JOBS = paths.JOBS                                              # テストでは CONSOLE_JOBS で差し替える
KB = REPO / "kanban" / "bin" / "kb"
KIT = REPO / "workflow" / "kit"
WORKFLOWS = KIT / "workflows"
ROLES = KIT / "roles"
KB_ROOT = paths.KB_ROOT
DB = KB_ROOT / "kanban.db"
RUNS = paths.RUNS
LOGS = paths.LOGS
SANDBOX_STATE = pathlib.Path.home() / ".config" / "sandbox" / "state.json"
SANDBOX_PJ_DIR = pathlib.Path.home() / ".config" / "sandbox" / "pj"
POOL_PER_PJ = 3
STATUSES = ["todo", "in_progress", "review", "blocked", "done"]
STATUS_LABEL = {"todo": "未着手", "in_progress": "実行中", "review": "レビュー待ち", "blocked": "人間待ち", "done": "完了"}
# 画面から読めるファイルの根（これ以外は 403）
READ_ROOTS = [RUNS, KB_ROOT / "tickets", LOGS, REPO / "workflow" / "kit", *paths.PROJECT_DIRS, JOBS]


# ---------- 時刻（ADR-0026: 記録はオフセット付き ISO 8601。オフセットの無い古い記録は書いたホスト＝ここの時間帯とみなす）
def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def ts_dt(s):
    """ISO 8601 の文字列を時間帯付きの datetime にする。オフセットが無ければこのホストの時間帯を補う"""
    d = datetime.datetime.fromisoformat(s)
    return d if d.tzinfo is not None else d.astimezone()


def ts_aware(s):
    """画面に出す時刻を「オフセット付き ISO 8601」に揃える。日時として読めないもの（v0 の自由文など）はそのまま返す。
       これが無いとブラウザーは naive な時刻を自分の時間帯として読み、サーバーと時間帯が違うだけで経過時間が時差ぶんずれる（チケット 235）"""
    if not isinstance(s, str) or not s: return s
    try: return ts_dt(s).isoformat(timespec="seconds")
    except ValueError: return s


def ts_file(p):
    """ファイルの更新時刻。オフセット付きで返す"""
    return datetime.datetime.fromtimestamp(pathlib.Path(p).stat().st_mtime).astimezone().isoformat(timespec="seconds")


def ts_keys(d, *keys):
    """dict の指定した鍵だけ ts_aware に通した新しい dict（元は変えない）。d が dict でなければそのまま返す"""
    if not isinstance(d, dict): return d
    return {**d, **{k: ts_aware(d[k]) for k in keys if k in d}}


def tz_info():
    """このサーバーの時間帯。画面がブラウザーとの違いを言うために使う"""
    d = datetime.datetime.now().astimezone()
    off = d.isoformat()[-6:]
    name = d.tzname() or ""
    plain = not name or name.upper() in ("UTC", "GMT") or name[0] in "+-"
    return {"name": name, "offset": off, "label": f"UTC{off}" if plain else f"{name} UTC{off}"}


def after(a, b):
    """ISO 8601 の日時 a が b より後か。どちらかが無い・読めないときは False（比較を諦めて安全側）。
       オフセットの有無が混ざっても比べられるよう、入口で時間帯を補う（混在は例外になり黙って False になっていた）"""
    try: return ts_dt(a) > ts_dt(b)
    except (TypeError, ValueError): return False


def load_yaml(p):
    try:
        import yaml
        with open(p, encoding="utf-8") as f: return yaml.safe_load(f) or {}
    except Exception as e:
        return {"_error": str(e)}


def child_env():
    env = dict(os.environ)
    # sandbox は ~/.local/bin の symlink。無いときの保険として末尾に足す（先頭に足すと python3 が別物になり、runner の yaml が見つからなくなる。2026-09-06 に踏んだ）
    extra = [str(pathlib.Path.home() / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin"]
    env["PATH"] = ":".join([env.get("PATH", "")] + [d for d in extra if d not in env.get("PATH", "").split(":")])
    env["PYTHONUNBUFFERED"] = "1"
    return env


# ---------- kanban（読み取り専用）
def db():
    if not DB.exists(): return None
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


ROW_TIME_COLS = ("created", "updated", "at")   # kanban の時刻の列。読むときにオフセットを補う（ADR-0026）


def rows(q, p=()):
    c = db()
    if c is None: return []
    try: return [ts_keys(dict(r), *ROW_TIME_COLS) for r in c.execute(q, p).fetchall()]
    finally: c.close()


def pjs():
    return paths.projects()


def project_yml(pj):
    d = paths.project_dir(pj)
    return (d / "project.yml") if d else (paths.PROJECT_DIRS[0] / pj / "project.yml")


def _names(d, suffix):
    """kit のディレクトリを走査して候補名を返す。`.` / `_` 始まり（macOS の `._bug.yml` など）とディレクトリは候補にしない"""
    return sorted(p.stem for p in pathlib.Path(d).glob("*" + suffix) if p.is_file() and not p.name.startswith((".", "_")))


def kinds(d=None):
    return _names(d or WORKFLOWS, ".yml")


def roles(d=None):
    return _names(d or ROLES, ".md")


def kind_desc(ks=None):
    """種別 → workflow yml の description。画面が種別の用途を出すために使う"""
    return {k: (load_yaml(WORKFLOWS / f"{k}.yml").get("description") or "") for k in (ks if ks is not None else kinds())}


def kb(*args, stdin=None):
    """kb を同期で呼ぶ（数秒で終わる操作だけ）。戻り: (rc, stdout, stderr)"""
    r = subprocess.run([str(KB), *map(str, args)], text=True, capture_output=True, errors="replace", input=stdin, env=child_env(), cwd=str(REPO))
    return r.returncode, r.stdout, r.stderr


# ---------- runs
def ts_state(s):
    """runner の state.json の時刻を揃える（画面に出す写しだけ。ファイルは書き換えない）"""
    if not isinstance(s, dict): return s
    out = ts_keys(s, "started", "finished")
    out["current"] = ts_keys(s.get("current"), "since")
    if isinstance(s.get("history"), list): out["history"] = [ts_keys(h, "at") for h in s["history"]]
    return out


def run_summary(d):
    st = d / "state.json"
    s = {}
    if st.exists():
        try: s = json.loads(st.read_text(encoding="utf-8"))
        except Exception as e: s = {"_error": str(e)}
    hist = s.get("history") or []
    # state.json が無い run（VM 貸出前に止まった残骸）は「開始前」。実行中と混ぜない（チケット 220）
    status = "not_started" if not st.exists() else "finished" if s.get("finished") else "running"
    return {"name": d.name, "kind": "v1", "status": status, "pj": s.get("pj"), "task": s.get("task"), "workflow": s.get("workflow"),
            "branch": s.get("branch"), "base": s.get("base"), "started": ts_aware(s.get("started")), "finished": ts_aware(s.get("finished")),
            "elapsed_s": s.get("elapsed_s"), "result": s.get("result"), "pr_url": s.get("pr_url"), "wip_branch": s.get("wip_branch"),
            "next": s.get("next"), "current": ts_keys(s.get("current"), "since"), "steps_done": len(hist), "last_ok": hist[-1]["ok"] if hist else None,
            "dry": d.name.endswith("-dry"), "attempt": bool(re.search(r"-attempt\d+$", d.name)),
            "mtime": ts_file(st if st.exists() else d)}


def v0_summary(f):
    head = f.read_text(encoding="utf-8", errors="replace").splitlines()[:8]
    m = re.match(r"^#\s*spin-v0:\s*(\S+)\s*/\s*task\s*(\S+)", head[0] if head else "")
    started = next((l.split(":", 1)[1].strip() for l in head if l.startswith("- 日時")), None)
    return {"name": f.name, "kind": "v0", "status": "finished", "pj": m.group(1) if m else None, "task": m.group(2) if m else None, "workflow": "spin-v0",
            "started": ts_aware(started), "finished": None, "result": None, "pr_url": None, "steps_done": None, "dry": False, "attempt": False,
            "mtime": ts_file(f)}


def list_runs():
    out = []
    if not RUNS.exists(): return out
    for p in RUNS.iterdir():
        if p.is_dir() and not p.name.startswith("."): out.append(run_summary(p))
        elif p.is_file() and p.suffix == ".md" and p.name.startswith("20"): out.append(v0_summary(p))
    out.sort(key=lambda r: (r.get("started") or r.get("mtime") or ""), reverse=True)
    return out


# ---------- 実行記録の要約（ADR-0025: 停止の理由は表示側で導く。runner の state.json は変えない）
GATE_FAIL = re.compile(r"^FAIL (\S+)")
STEP_LOG = re.compile(r"^(agent|code)-(.+)-(\d+)\.log$")
WORK_DOC = re.compile(r"^work/[^/]+\.(md|txt)$")


def gate_fails(p):
    """work/gates.txt から赤いゲートの名前を拾う。kit/steps/gates.sh の書式 `FAIL <ゲート名>` に依る。
       赤があると同じファイルの後ろに `=== <ゲート>.log (tail 60)` とログ末尾が続くので、そこから先は見ない（ログ中の FAIL を拾わないため）"""
    out = []
    try: text = p.read_text(encoding="utf-8", errors="replace")
    except OSError: return out
    for line in text.splitlines():
        if line.startswith("=== "): break
        m = GATE_FAIL.match(line)
        if m: out.append(m.group(1))
    return out


def run_outcome(d, s, state, wf, files):
    """「結果・止まった工程・理由の在り処」を、今ある記録（history / loops / gates.txt / ログの有無）だけから導く。
       runner は停止の理由を自由文で残さないので、導けないものは unknown（＝画面では「記録にありません」）にする"""
    o = {"reason": "unknown", "stopped_step": None, "stopped_index": None, "detail_file": None,
         "gate_fails": [], "fail_count": 0, "loops_hit": False, "pr_url": s.get("pr_url")}
    if s.get("kind") == "v0": o["reason"] = "v0"; return o
    if s.get("status") == "not_started": o["reason"] = "not_started"; return o
    state = state or {}
    hist = state.get("history") or []
    if state.get("_error") or (not hist and s.get("finished")): return o
    if s.get("status") == "running":
        o["reason"] = "running"; o["stopped_step"] = state.get("next"); return o
    if not hist: return o
    if s.get("pr_url"): o["reason"] = "pr_created"; return o
    last = hist[-1]
    if last.get("ok") is False:
        step = last.get("step"); o["stopped_step"] = step; o["stopped_index"] = len(hist) - 1
        o["fail_count"] = sum(1 for e in hist if e.get("step") == step and e.get("ok") is False)
        sd = next((x for x in (wf or {}).get("steps") or [] if x.get("id") == step), {})
        on_fail = sd.get("on_fail")
        if isinstance(on_fail, dict) and on_fail.get("goto"):
            o["loops_hit"] = (state.get("loops") or {}).get(f"{step}->{on_fail['goto']}", 0) >= on_fail.get("max_loops", 1)
        o["reason"] = "loop_limit" if o["loops_hit"] else "step_failed"
        by_name = {f["name"]: f for f in files}
        if "gates.txt" in (sd.get("outputs") or []) and "work/gates.txt" in by_name:
            o["gate_fails"] = gate_fails(d / "work" / "gates.txt")
            o["detail_file"] = by_name["work/gates.txt"]["path"]
        for kind in ("agent", "code"):                          # ログが消えている run では detail_file は None のまま
            nm = f"{kind}-{step}-{o['stopped_index']}.log"
            if not o["detail_file"] and nm in by_name: o["detail_file"] = by_name[nm]["path"]
        return o
    o["reason"] = {"end": "ended", "human": "waiting"}.get(s.get("result"), "unknown")
    return o


def run_groups(files, wf):
    """ファイルを目的別に分ける。成果物の名前は決め打ちせず workflow 定義の outputs から引く（workflow が増えても崩れない）。
       kind は role 名（researcher / planner / …）か code step の id（gates）。分からないものは None で名前だけ出す"""
    by_name = {f["name"]: f for f in files}
    used, arts = set(), []

    def add(name, kind, step=None):
        f = by_name.get(name)
        if not f or name in used: return False
        used.add(name); arts.append({**f, "step": step, "kind": kind}); return True

    add("work/ticket.md", "ticket") or add("ticket.md", "ticket")   # VM から回収した写しが無ければ runner が置いた元を出す
    for stp in (wf or {}).get("steps") or []:
        for out in stp.get("outputs") or []:
            if out in ("git", "pr_url"): continue
            add(f"work/{out}", stp.get("role") or stp.get("id"), stp.get("id"))
    for f in files:                                             # outputs に無い work/*.md も成果物として出す（v0 や古い run）
        if f["name"] not in used and WORK_DOC.match(f["name"]): used.add(f["name"]); arts.append({**f, "step": None, "kind": None})
    logs, other = [], []
    for f in files:
        if f["name"] in used: continue
        m = STEP_LOG.match(f["name"])
        if m: logs.append({**f, "kind": m.group(1), "step": m.group(2), "index": int(m.group(3))})
        else: other.append(f)
    logs.sort(key=lambda x: (x["index"], x["name"]))
    return {"artifacts": arts, "step_logs": logs, "other": other}


def run_detail(name):
    d = RUNS / name
    if not d.exists() or not d.resolve().is_relative_to(RUNS.resolve()): return None
    if d.is_file():
        s = v0_summary(d)
        return {"summary": s, "files": [{"path": rel(d), "name": d.name, "size": d.stat().st_size}], "state": None, "workflow": None,
                "outcome": run_outcome(d, s, None, None, []), "groups": {"artifacts": [], "step_logs": [], "other": []}}
    s = run_summary(d)
    state = {}
    if (d / "state.json").exists():
        try: state = ts_state(json.loads((d / "state.json").read_text(encoding="utf-8")))
        except Exception as e: state = {"_error": str(e)}
    files = []
    for p in sorted(d.rglob("*")):
        if p.is_file(): files.append({"path": rel(p), "name": str(p.relative_to(d)), "size": p.stat().st_size, "mtime": ts_file(p)})
    wf = None
    if s.get("workflow"):
        wp = REPO / "workflow" / "kit" / "workflows" / f"{s['workflow']}.yml"
        if wp.exists(): wf = load_yaml(wp)
    # 対応するチケット
    tr = rows("SELECT id, title, status, pr, run, updated FROM tickets WHERE id = ?", (int(s["task"]),)) if s.get("task") and str(s["task"]).isdigit() else []
    ticket = tr[0] if tr else None
    # このコンソールから起動したジョブ
    jobs = [j for j in JobStore.list() if j.get("run_hint") == name or (j.get("ticket") and s.get("task") and str(j["ticket"]) == str(s["task"]))]
    try: outcome = run_outcome(d, s, state, wf, files)
    except Exception as e: outcome = {"reason": "unknown", "stopped_step": None, "stopped_index": None, "detail_file": None,
                                      "gate_fails": [], "fail_count": 0, "loops_hit": False, "pr_url": s.get("pr_url"), "_error": str(e)}
    return {"summary": s, "state": state, "files": files, "workflow": wf, "ticket": ticket, "jobs": jobs[:5],
            "outcome": outcome, "groups": run_groups(files, wf)}


def rel(p):
    """read_file に渡せるパス。リポジトリ内なら相対、外（workspace を外に置いた場合）なら絶対"""
    p = pathlib.Path(p).resolve()
    return str(p.relative_to(REPO.resolve())) if p.is_relative_to(REPO.resolve()) else str(p)


def read_file(relpath, tail=None, offset=None):
    p = (REPO / relpath).resolve()   # 絶対パスならそのまま（workspace がリポジトリ外でもよい。根の検査は下）
    if not any(p.is_relative_to(r.resolve()) for r in READ_ROOTS if r.exists()): return None, "この場所のファイルは表示できません（読めるのは runs / kanban/tickets / logs / workflow/kit / PJ 定義 / console/jobs の下だけです）"
    if not p.is_file(): return None, "ファイルが見つかりません"
    size = p.stat().st_size
    with open(p, "rb") as f:
        if offset is not None:
            f.seek(min(offset, size)); data = f.read()
            return {"path": relpath, "size": size, "offset": min(offset, size), "text": data.decode("utf-8", "replace"), "truncated": False}, None
        if tail and size > tail:
            f.seek(size - tail); data = f.read()
            return {"path": relpath, "size": size, "text": data.decode("utf-8", "replace"), "truncated": True}, None
        return {"path": relpath, "size": size, "text": f.read().decode("utf-8", "replace"), "truncated": False}, None


# ---------- sandbox
def sandbox_env():
    """~/.config/sandbox/env から表示に要る 2 項目だけ読む（トークン等の他の行は読まない）"""
    out = {"SB_DOMAIN": "sb.internal", "APP_PORT": "3000"}
    p = SANDBOX_STATE.parent / "env"
    if p.exists():
        for l in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^(SB_DOMAIN|APP_PORT)=\"?([^\"#\s]+)", l.strip())
            if m: out[m.group(1)] = m.group(2)
    return out


def parse_ls(text):
    """`sandbox ls` のジョブログを VM 1 台 = 1 件の dict に分ける。

    CLI は固定幅の printf で出す（sandbox/bin/sandbox の cmd_ls）。VM 名・IP・時刻に空白は入らないので空白で区切る。
    捨てる行: ジョブ先頭の `$ ...`、`[error]` のような注記、見出し（TASK ...）、列数が合わない行。
    読めない行は黙って捨てる（生ログはジョブの記録にそのまま残る）。task が `-`（貸出なし）のときは None。
    """
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s[0] in "$[": continue
        f = s.split()
        if f[0] == "TASK" or not 5 <= len(f) <= 6: continue
        task, name, vmid, ip, status = f[:5]
        out.append({"task": None if task == "-" else task, "name": name, "vmid": vmid, "ip": ip,
                    "status": status, "since": f[5] if len(f) == 6 else None})
    return out


def sandbox_view():
    lent = {}
    if SANDBOX_STATE.exists():
        try: lent = {k: ts_keys(v, "since") for k, v in json.loads(SANDBOX_STATE.read_text(encoding="utf-8")).items()}
        except Exception as e: lent = {"_error": str(e)}
    tpl = []
    for pj in pjs():
        py = project_yml(pj)
        y = load_yaml(py) if py.exists() else None
        n = sum(1 for v in lent.values() if isinstance(v, dict) and v.get("pj") == pj)
        tpl.append({"pj": pj, "project_yml": py.exists(), "repo": (y or {}).get("repo"), "base_branch": (y or {}).get("base_branch"),
                    "display_name": (y or {}).get("display_name", pj), "token_file": (SANDBOX_PJ_DIR / f"{pj}.env").exists(),
                    "lent": n, "pool": POOL_PER_PJ, "known_red_gates": (y or {}).get("known_red_gates") or []})
    ls_jobs = [j for j in JobStore.list() if j.get("kind") == "sandbox-ls"]   # 新しい順
    last_ls = ls_jobs[0] if ls_jobs else None                                 # 直近（失敗・実行中も含む）。画面は取得中 / 成功 / 失敗 / 未取得を分けて出す
    last_ok_ls = next((j for j in ls_jobs if j.get("rc") == 0), None)          # 表に出せる最後の成功。失敗しても前回の表は残す
    vms = []
    if last_ok_ls:
        log = JOBS / last_ok_ls["id"] / "log"
        if log.exists(): vms = parse_ls(log.read_text(encoding="utf-8", errors="replace"))
    e = sandbox_env()
    urls = {t: f"http://task-{t}.{e['SB_DOMAIN']}:{e['APP_PORT']}" for t, v in lent.items() if isinstance(v, dict)}
    return {"lent": lent, "urls": urls, "templates": tpl, "pool_per_pj": POOL_PER_PJ, "state_file": str(SANDBOX_STATE),
            "last_ls": last_ls, "last_ok_ls": last_ok_ls, "vms": vms}


# ---------- jobs
class Conflict(Exception):
    pass


@contextlib.contextmanager
def _flock():
    """jobs/.lock の flock。console（HTTP）と mcp（stdio）が別プロセスで同じ jobs/ を触るので、二重起動の判定と meta の読み書きはこれで直列化する"""
    JOBS.mkdir(parents=True, exist_ok=True)
    with open(JOBS / ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(f, fcntl.LOCK_UN)


class JobStore:
    lock = threading.Lock()
    procs = {}   # id -> Popen

    @classmethod
    def _meta(cls, jid):
        return JOBS / jid / "meta.json"

    @classmethod
    def list(cls):
        out = []
        if not JOBS.exists(): return out
        for d in JOBS.iterdir():
            m = cls._meta(d.name)
            if m.exists():
                try: out.append(ts_keys(json.loads(m.read_text(encoding="utf-8")), "started", "finished", "stop_requested"))
                except Exception: pass
        out.sort(key=lambda j: j.get("started", ""), reverse=True)
        return out

    @classmethod
    def get(cls, jid):
        m = cls._meta(jid)
        if not m.exists(): return None
        return json.loads(m.read_text(encoding="utf-8"))

    @classmethod
    def save(cls, meta):
        m = cls._meta(meta["id"]); m.parent.mkdir(parents=True, exist_ok=True)
        tmp = m.with_suffix(".tmp"); tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(m)

    @classmethod
    def running(cls):
        return [j for j in cls.list() if j.get("rc") is None and j.get("state") == "running"]

    @classmethod
    def start(cls, kind, cmd, label, ticket=None, stdin_text=None, run_hint=None, cwd=None, conflict=None):
        """conflict: 実行中ジョブ j を受けて衝突なら理由文字列を返す関数。ロックの中で判定するので同時リクエストでも二重にならない"""
        with cls.lock, _flock():
            if conflict:
                for j in cls.running():
                    why = conflict(j)
                    if why: raise Conflict(why)
            base = f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{kind}"
            jid, n = base, 1
            while (JOBS / jid).exists(): n += 1; jid = f"{base}-{n}"
            d = JOBS / jid; d.mkdir(parents=True)
            stdin_path = None
            if stdin_text is not None:
                stdin_path = d / "stdin.txt"; stdin_path.write_text(stdin_text, encoding="utf-8")
                cmd = [(str(stdin_path) if a == "{stdin}" else a) for a in cmd]
            log = open(d / "log", "ab")
            log.write(f"$ {' '.join(cmd)}\n".encode()); log.flush()
            p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(cwd or REPO), env=child_env(), start_new_session=True)
            meta = {"id": jid, "kind": kind, "label": label, "cmd": cmd, "ticket": ticket, "run_hint": run_hint, "pid": p.pid,
                    "started": now(), "finished": None, "rc": None, "state": "running"}
            cls.save(meta); cls.procs[jid] = p
            threading.Thread(target=cls._wait, args=(jid, p, log), daemon=True).start()
            return meta

    @classmethod
    def _wait(cls, jid, p, log):
        rc = p.wait(); log.close()
        with cls.lock, _flock():   # stop() の書き込みと競合しないように、読み直し→書き込みをロックの中で
            meta = cls.get(jid)
            if meta is None: cls.procs.pop(jid, None); return   # 記録が消されていたら（テストの後片付け等）何もしない
            meta.update({"rc": rc, "finished": now(), "state": "stopped" if meta.get("stop_requested") else ("done" if rc == 0 else "failed")})
            cls.save(meta); cls.procs.pop(jid, None)

    @classmethod
    def stop(cls, jid):
        with cls.lock, _flock():   # 先にフラグを書いてから殺す（殺した瞬間に _wait が走る）
            meta = cls.get(jid)
            if not meta or meta.get("rc") is not None: return False, "このジョブは既に終わっています"
            meta["stop_requested"] = now(); cls.save(meta)
        try: os.killpg(os.getpgid(meta["pid"]), signal.SIGTERM)
        except ProcessLookupError: return False, "プロセスが見つかりません（既に終わっている可能性があります）"
        return True, "SIGTERM を送りました。終わるまで数秒かかることがあります"

    @classmethod
    def reconcile(cls):
        """起動時: 前のコンソールが残した running を実勢に合わせる"""
        for j in cls.list():
            if j.get("rc") is None and j.get("state") == "running" and j["id"] not in cls.procs:
                alive = False
                try: os.kill(j["pid"], 0); alive = True
                except OSError: pass
                if not alive:
                    j.update({"state": "lost", "finished": now(), "note": "コンソール再起動時にプロセスが無かった（終了コード不明）"}); cls.save(j)
                else:
                    j["note"] = "前のプロセスが起動したジョブ。終了は pid の消滅で検知する（終了コードは不明）"; cls.save(j)
                    threading.Thread(target=cls._watch_orphan, args=(j["id"], j["pid"]), daemon=True).start()

    @classmethod
    def _watch_orphan(cls, jid, pid):
        """別プロセス（再起動前のコンソール等）が起動したジョブを、pid が消えるまで見張って終わりを記録する"""
        while True:
            try: os.kill(pid, 0)
            except OSError: break
            time.sleep(5)
        with cls.lock, _flock():
            meta = cls.get(jid)
            if meta and meta.get("rc") is None and meta.get("state") == "running":
                meta.update({"state": "ended", "finished": now(), "note": "終了を pid の消滅で検知（終了コード不明）。kb run なら run の state.json と kb の状態が正"}); cls.save(meta)


# ---------- overview
def overview():
    counts = {s: 0 for s in STATUSES}
    for r in rows("SELECT status, COUNT(*) n FROM tickets GROUP BY status"): counts[r["status"]] = r["n"]
    running = JobStore.running()
    runs = [r for r in list_runs() if r["kind"] == "v1" and not r.get("dry")]
    active = [r for r in runs if r["status"] == "running"]
    not_started = [r for r in runs if r["status"] == "not_started"]
    lent = {}
    if SANDBOX_STATE.exists():
        try: lent = json.loads(SANDBOX_STATE.read_text(encoding="utf-8"))
        except Exception: lent = {}
    return {"counts": counts, "labels": STATUS_LABEL, "jobs_running": len(running), "jobs": running[:6], "runs_active": active[:6], "runs_not_started": {"n": len(not_started), "runs": not_started[:6]},
            "lent": len([v for v in lent.values() if isinstance(v, dict)]), "db": DB.exists(), "kb_root": str(KB_ROOT), "paths": paths.describe(), "now": now(), "tz": tz_info()}




# ---------- 操作（HTTP の console と stdio の mcp が共有する。判定はここに 1 つ）
class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg); self.code = code


def tickets_list(pj=None, status=None, all_=True):
    sql = "SELECT * FROM tickets WHERE 1=1"; p = []
    if pj: sql += " AND pj = ?"; p.append(pj)
    if status: sql += " AND status = ?"; p.append(status)
    elif not all_: sql += " AND status != 'done'"
    ks = kinds()
    ps = pjs()
    # 起票画面が「配車すると人間待ちになる PJ」を選ぶ前に言えるように、判定は sandbox / チケットと同じ project.yml の有無で返す
    return {"tickets": rows(sql + " ORDER BY id", p), "pjs": ps, "pj_ready": {x: project_yml(x).exists() for x in ps},
            "kinds": ks, "kind_desc": kind_desc(ks), "labels": STATUS_LABEL}


def ticket_next(pj=None):
    """配車で次に回る todo を 1 件（kb next --json）。画面が「配車する」を押す前に影響を見せるために使う。無ければ None"""
    rc, out, err = kb("next", *(["--pj", pj] if pj else []), "--json")
    if rc != 0: raise ApiError((err or out).strip() or f"kb next が失敗 rc={rc}")
    out = out.strip()
    return {"next": json.loads(out) if out else None}


def ticket_detail(tid):
    t = rows("SELECT * FROM tickets WHERE id = ?", (tid,))
    if not t: raise ApiError(f"チケット {tid} は見つかりません", 404)
    t = t[0]; f = KB_ROOT / t["file"]
    py = project_yml(t["pj"])
    t["repo"] = load_yaml(py).get("repo") if py.exists() else None
    body = f.read_text(encoding="utf-8", errors="replace") if f.exists() else None
    hist = rows("SELECT at, field, old, new FROM history WHERE ticket = ? ORDER BY id", (tid,))
    runs = [r for r in list_runs() if str(r.get("task")) == str(tid) and r.get("pj") == t["pj"]]
    jobs = [j for j in JobStore.list() if j.get("ticket") == tid][:10]
    ks = kinds()
    return {"ticket": t, "body": body, "file": str(f.relative_to(REPO)) if f.exists() and f.resolve().is_relative_to(REPO.resolve()) else str(f),
            "history": hist, "runs": runs, "jobs": jobs, "kinds": ks, "kind_desc": kind_desc(ks), "labels": STATUS_LABEL, "project_yml": py.exists()}


def ticket_action(tid, b):
    act = b.get("action")
    if act in ("start", "review", "done", "reopen", "block"):
        if act == "block" and not b.get("note"): raise ApiError("人間待ちにするには、何を待っているかを note に書いてください")
        args = [act, tid] + (["--note", b["note"]] if b.get("note") else [])
    elif act == "set":
        args = ["set", tid]
        for k in ("status", "pr", "note", "kind"):
            if b.get(k) not in (None, ""): args += [f"--{k}", b[k]]
        if b.get("run"): args += ["--run", b["run"]]
        if len(args) == 2: raise ApiError("変える項目がありません。status / pr / note / kind / run のどれかを指定してください")
    elif act == "sync":
        args = ["sync", tid] + (["--run", b["run"]] if b.get("run") else [])
    else: raise ApiError(f"操作 {act} はありません。start / review / done / reopen / block / set / sync のどれかを指定してください")
    rc, out, err = kb(*args)
    if rc != 0: raise ApiError((err or out).strip() or f"kb {act} が失敗 rc={rc}")
    return {"rc": rc, "stdout": out, "stderr": err}


def sync_preview(tid, run=None):
    """「実行記録に状態を合わせる」を押す前の下見。kb sync --dry-run を呼び、前後（状態・メモ）と、
    その run が終わった後にチケットが人手で更新されたかを返す。判定の正本は kb 側（ここには写さない）"""
    t = rows("SELECT * FROM tickets WHERE id = ?", (tid,))
    if not t: raise ApiError(f"チケット {tid} は見つかりません", 404)
    t = t[0]
    run = run or t["run"]
    if not run: raise ApiError(f"チケット {tid} に run がありません。チケットの run を設定するか、--run で指定してください")
    rc, out, err = kb("sync", tid, "--run", run, "--dry-run")
    if rc != 0: raise ApiError((err or out).strip() or f"kb sync --dry-run が失敗 rc={rc}")
    try: p = json.loads(out.strip().splitlines()[-1])
    except Exception: raise ApiError("実行記録を読み直した結果を読めませんでした。もう一度お試しください")
    b, a = p["before"], p["after"]
    p["run_finished"] = ts_aware(p.get("run_finished"))
    p["before"] = ts_keys(b, "updated")
    p["ticket"] = {k: t[k] for k in ("id", "title", "pj", "status", "note", "run", "updated")}
    p["updated_after_run"] = after(t["updated"], p.get("run_finished"))
    p["changes"] = b.get("status") != a.get("status") or (b.get("note") or "") != (a.get("note") or "")
    p["labels"] = STATUS_LABEL
    return p


def ticket_run(tid, b):
    t = rows("SELECT * FROM tickets WHERE id = ?", (tid,))
    if not t: raise ApiError(f"チケット {tid} は見つかりません", 404)
    cmd = [str(KB), "run", str(tid)]
    if b.get("workflow"): cmd += ["--workflow", b["workflow"]]
    for f in ("dry_run", "keep", "resume"):
        if b.get(f): cmd.append("--" + f.replace("_", "-"))
    label = f"kb run {tid}" + (" --dry-run" if b.get("dry_run") else "") + (" --resume" if b.get("resume") else "")
    hint = f"{datetime.date.today().isoformat()}-{t[0]['pj']}-{tid}" + ("-dry" if b.get("dry_run") else "")
    if b.get("resume") and not b.get("dry_run") and t[0].get("run"):
        hint = t[0]["run"]
    same = lambda j: f"チケット {tid} のジョブ {j['id']} が実行中です。終わるのを待つか、ジョブを止めてから実行してください" if j.get("ticket") == tid and j.get("kind") in ("kb-run", "dispatch", "sandbox-release") else None
    return {"job": JobStore.start("kb-run", cmd, label, ticket=tid, run_hint=hint, conflict=same)}


def ticket_new(b):
    for k in ("pj", "kind", "title"):
        if not b.get(k): raise ApiError(f"{k} を指定してください")
    args = ["new", b["pj"], b["kind"], b["title"][:70], "--body", "-"]
    if b.get("pr"): args += ["--pr", str(b["pr"])]
    if b.get("note"): args += ["--note", b["note"]]
    rc, out, err = kb(*args, stdin=b.get("body") or "")
    if rc != 0: raise ApiError((err or out).strip() or f"kb new が失敗 rc={rc}")
    tid = int(out.split()[0]) if out.split() and out.split()[0].isdigit() else None
    return {"rc": rc, "stdout": out, "stderr": err, "id": tid}


def op_intake(b):
    text = (b.get("text") or "").strip()
    if not text: raise ApiError("依頼文が空です。取り込む文章を text に入れてください")
    cmd = [str(REPO / "glue" / "bin" / "intake"), "{stdin}"]
    if b.get("pj"): cmd += ["--pj", b["pj"]]
    if b.get("kind"): cmd += ["--kind", b["kind"]]
    if b.get("dry_run"): cmd.append("--dry-run")
    label = "intake" + (" --dry-run" if b.get("dry_run") else "") + f"（{text[:30]}…）"
    return {"job": JobStore.start("intake", cmd, label, stdin_text=text + "\n")}


def op_dispatch(b):
    cmd = [str(REPO / "glue" / "bin" / "dispatch")]
    if b.get("pj"): cmd += ["--pj", b["pj"]]
    if b.get("once"): cmd.append("--once")
    elif b.get("max"): cmd += ["--max", str(int(b["max"]))]
    if b.get("dry_run"): cmd.append("--dry-run")
    serial = (lambda j: None) if b.get("dry_run") else (lambda j: f"dispatch（ジョブ {j['id']}）が既に実行中です。直列で回す約束なので、終わるまで待ってください" if j.get("kind") == "dispatch" else None)
    return {"job": JobStore.start("dispatch", cmd, "dispatch " + " ".join(cmd[1:]), conflict=serial)}


def op_sandbox_ls():
    return {"job": JobStore.start("sandbox-ls", ["sandbox", "ls"], "sandbox ls")}


def op_sandbox_release(b):
    task = str(b.get("task") or "")
    if not re.match(r"^\d{3,}$", task): raise ApiError("チケット番号（task）は 3 桁以上の数字で指定してください")
    busy = lambda j: f"チケット {task} のジョブ {j['id']} が実行中です。先にジョブを止めてから返却してください" if j.get("ticket") == int(task) else None
    return {"job": JobStore.start("sandbox-release", ["sandbox", "release", task], f"sandbox release {task}", ticket=int(task), conflict=busy)}


def job_view(jid, offset=0):
    j = JobStore.get(jid)
    if not j: raise ApiError(f"ジョブ {jid} は見つかりません", 404)
    j = ts_keys(j, "started", "finished", "stop_requested")
    data, _ = read_file(str(JOBS / j["id"] / "log"), offset=offset)   # JOBS はリポジトリ外でもよい（絶対パス。根の検査は read_file）
    # 過去のジョブを開いたとき、画面が「今」のチケットで案内を決められるように現在値を添える（古い復旧案内を主表示しないため）
    t = None
    if j.get("ticket"):
        r = rows("SELECT id, title, pj, status, note, run, updated FROM tickets WHERE id = ?", (j["ticket"],))
        if r:
            t = r[0]
            t["updated_after_job"] = after(t["updated"], j.get("finished"))
    return {"job": j, "log": data, "ticket": t}


def job_wait(jid, timeout_s=120):
    """ジョブが終わるまで待つ（MCP から使う。最大 timeout_s 秒）。終わらなければ state=running のまま返す"""
    t0 = time.time()
    while True:
        j = JobStore.get(jid)
        if not j: raise ApiError(f"ジョブ {jid} は見つかりません", 404)
        if j.get("state") != "running" or time.time() - t0 >= timeout_s: return ts_keys(j, "started", "finished", "stop_requested")
        time.sleep(1)


def op_job_stop(jid):
    ok, msg = JobStore.stop(jid)
    if not ok: raise ApiError(msg)
    return {"ok": True, "message": msg}


def logs_view():
    out = {}
    for name in ("intake", "dispatch"):
        data, _ = read_file(str(LOGS / f"{name}.log"), tail=200_000); out[name] = data
    return out


def config_view():
    wfs = []
    for k in kinds():
        y = load_yaml(WORKFLOWS / f"{k}.yml")
        wfs.append({"name": k, "description": y.get("description", ""), "steps": [{"id": s.get("id"), "role": s.get("role"), "code": s.get("code")} for s in y.get("steps", [])], "start": y.get("start")})
    routes = dict(l.split("=", 1) for l in (KIT / "routes.env").read_text().splitlines() if l and not l.startswith("#") and "=" in l)
    rs = roles()
    git = subprocess.run(["git", "status", "--short", "--branch"], cwd=str(REPO), text=True, capture_output=True, errors="replace").stdout
    return {"workflows": wfs, "routes": routes, "roles": rs, "templates": sandbox_view()["templates"], "kb_root": str(KB_ROOT), "repo": str(REPO), "paths": paths.describe(), "git": git}
