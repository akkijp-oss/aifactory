"""console/lib/core.py: aifactory の読み書きの正本。bin/console（HTTP）と bin/mcp（stdio）が共有する。

- 読む: kanban.db（読み取り専用）/ runs/ / ~/.config/sandbox/state.json / intake・dispatch のログ
- 動かす: kb / intake / dispatch / sandbox を子プロセスで。長いものは JobStore（console/jobs/）。判定（二重起動・入力検査）はここに 1 つ
- 置き場は lib/aifactory_paths.py（AIFACTORY_WORKSPACE。KB_ROOT / CONSOLE_JOBS で個別に差し替え可）
"""
import base64, contextlib, datetime, fcntl, json, os, pathlib, re, shutil, signal, sqlite3, subprocess, sys, tempfile, threading, time

HERE = pathlib.Path(__file__).resolve().parent.parent          # console/（lib/ の親）
REPO = HERE.parent
sys.path.insert(0, str(REPO / "lib")); import aifactory_paths as paths, aifactory_attachments as attachments
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
def sandbox_state_path():
    """貸出台帳（state.json）の置き場。環境変数 SANDBOX_STATE が正（glue/bin/dispatch・workflow/bin/run と同じ規則）。

    テナント運用では sandbox CLI が `<t>.state.json` を使うので、ここだけ固定パスを読むと
    「CLI では貸出中なのに MCP の lent は {}」になる（チケット 336 の 3 番目）。
    """
    return pathlib.Path(os.environ.get("SANDBOX_STATE") or (pathlib.Path.home() / ".config" / "sandbox" / "state.json"))


def sandbox_keys_path():
    """Claude の鍵プール（keys.json）の置き場。sandbox CLI と同じ規則で、環境変数 SANDBOX_KEYS が正。
    無ければ台帳（state.json）の隣（テナント運用では <t>.keys.json）。値はここでは読まない（読むのは sandbox CLI だけ。ADR-0044）"""
    v = os.environ.get("SANDBOX_KEYS")
    if v: return pathlib.Path(v)
    s = sandbox_state_path()
    return s.parent / (s.name[:-len("state.json")] + "keys.json" if s.name.endswith("state.json") else "keys.json")


SANDBOX_STATE = sandbox_state_path()
SANDBOX_KEYS = sandbox_keys_path()
SANDBOX_PJ_DIR = pathlib.Path.home() / ".config" / "sandbox" / "pj"
POOL_PER_PJ = int(os.environ.get("SANDBOX_POOL_PER_PJ") or 3)   # 「定義台数」の正本は glue/bin/dispatch と同じ環境変数（241）
LS_STALE_S = 600                                                # `sandbox ls` の結果がこれより古ければ「古い」と添える（229）
STATUSES = ["todo", "in_progress", "review", "blocked", "done"]
STATUS_LABEL = {"todo": "未着手", "in_progress": "実行中", "review": "レビュー待ち", "blocked": "人間待ち", "done": "完了"}
# 画面から読めるファイルの根（これ以外は 403）
READ_ROOTS = [RUNS, KB_ROOT / "tickets", LOGS, REPO / "workflow" / "kit", *paths.PROJECT_DIRS, JOBS, paths.ATTACHMENTS]
READ_IMAGE_MAX = 4 * 1024 * 1024        # read_file が画像を base64 で返す上限。base64 にすると約 1.33 倍に膨らむ（MCP の 1 応答に載る大きさ）
# MCP の ticket_attach(path=...) が読んでよい根。添付の判定ではなく「入口」の判定なので lib には置かない。
# `.` で始まる要素を含むパスは弾くので ~/.config/aifactory/ctl.env・~/.ssh はここで落ちる
ATTACH_PATH_ROOTS = [pathlib.Path.home(), pathlib.Path("/tmp")]


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


CTL_ENV = pathlib.Path(os.environ.get("AIFACTORY_CTL_ENV") or (pathlib.Path.home() / ".config" / "aifactory" / "ctl.env"))


def load_ctl_env(path=None):
    """制御系の secrets（~/.config/aifactory/ctl.env）を環境に補う。systemd の console は EnvironmentFile で読むが、
    ssh 越しに起動する bin/mcp は誰も読まないので同じ結果にならなかった（チケット 249）。
    既に非空の環境変数は上書きしない（手動起動・テストでの指定を殺さないため）。値は出力しない。戻り: 補った key の名前"""
    p = pathlib.Path(path) if path else CTL_ENV
    if not p.exists(): return []
    added = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if v[:1] in ("'", '"') and v[-1:] == v[:1] and len(v) >= 2: v = v[1:-1]
        if not k or not v or os.environ.get(k): continue
        os.environ[k] = v; added.append(k)
    global SANDBOX_STATE, SANDBOX_KEYS
    SANDBOX_STATE = sandbox_state_path()   # ctl.env に SANDBOX_STATE があれば、それを読んでから台帳の場所を決め直す
    SANDBOX_KEYS = sandbox_keys_path()
    return added


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
RUN_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-(.+?)-(\d+)(?:-dry)?(?:-attempt\d+)?$")


def ts_state(s):
    """runner の state.json の時刻を揃える（画面に出す写しだけ。ファイルは書き換えない）"""
    if not isinstance(s, dict): return s
    out = ts_keys(s, "started", "finished")
    out["current"] = ts_keys(s.get("current"), "since")
    if isinstance(s.get("history"), list): out["history"] = [ts_keys(h, "at") for h in s["history"]]
    return out


def resume_command(task, s):
    """human で止まった run を、新しい VM で続きから回すコマンド（チケット 333）。文言は console が組み、記録（state.json）には
    事実だけを置く（ADR-0025 / ADR-0036）。続けるのに要る事実（wip ブランチ・やり直す step）が揃っていなければ None"""
    wip, step = s.get("wip_branch"), s.get("resume_step")
    if s.get("result") != "human" or not (task and wip and step): return None
    return f"kb run {task} --from {step} --branch {wip}"


def run_summary(d):
    st = d / "state.json"
    s = {}
    if st.exists():
        try: s = json.loads(st.read_text(encoding="utf-8"))
        except Exception as e: s = {"_error": str(e)}
    hist = s.get("history") or []
    # state.json が無い run（VM 貸出前に止まった残骸）は「開始前」。実行中と混ぜない（チケット 220）
    # 読めない state.json も「開始前」に寄せ、読めなかったことを state_error で添える（実行中に見せない。チケット 236）
    status = "not_started" if not st.exists() or s.get("_error") else "finished" if s.get("finished") else "running"
    # 記録に PJ とチケット番号が無くても、run 名 <日付>-<pj>-<チケット> から補う（ヘッダーの導線と PJ の絞り込みのため）
    pj, task, from_name = s.get("pj"), s.get("task"), False
    if not pj or not task:
        m = RUN_NAME.match(d.name)
        if m:
            if not pj: pj, from_name = m.group(1), True
            if not task: task, from_name = m.group(2), True
    return {"name": d.name, "kind": "v1", "status": status, "pj": pj, "task": task, "from_name": from_name,
            "state_error": s.get("_error"), "runner": None, "workflow": s.get("workflow"),
            "branch": s.get("branch"), "base": s.get("base"), "started": ts_aware(s.get("started")), "finished": ts_aware(s.get("finished")),
            "elapsed_s": s.get("elapsed_s"), "result": s.get("result"), "pr_url": s.get("pr_url"), "wip_branch": s.get("wip_branch"),
            "resume_step": s.get("resume_step"), "resumed_from": s.get("resumed_from"), "resume": resume_command(task, s),
            "human": s.get("human"), "merged": s.get("merged"),
            "next": s.get("next"), "current": ts_keys(s.get("current"), "since"), "steps_done": len(hist), "last_ok": hist[-1]["ok"] if hist else None,
            "dry": d.name.endswith("-dry"), "attempt": bool(re.search(r"-attempt\d+$", d.name)),
            "mtime": ts_file(st if st.exists() else d)}


def v0_summary(f):
    head = f.read_text(encoding="utf-8", errors="replace").splitlines()[:8]
    m = re.match(r"^#\s*spin-v0:\s*(\S+)\s*/\s*task\s*(\S+)", head[0] if head else "")
    started = next((l.split(":", 1)[1].strip() for l in head if l.startswith("- 日時")), None)
    return {"name": f.name, "kind": "v0", "status": "finished", "pj": m.group(1) if m else None, "task": m.group(2) if m else None,
            "from_name": False, "state_error": None, "runner": None, "workflow": "spin-v0",
            "started": ts_aware(started), "finished": None, "result": None, "pr_url": None, "steps_done": None, "dry": False, "attempt": False,
            "resume_step": None, "resumed_from": None, "resume": None, "human": None,
            "mtime": ts_file(f)}


def run_liveness(s, jobs, tickets):
    """「実行中」に見える run が、実は runner の居ない残骸かを判定して status を abandoned にする（チケット 236）。

    runner は落ちるときに state.json へ finished を書けないことがある（take の失敗・SIGTERM・VM の再起動）。
    その run を実行中のまま出すと「待っていれば進む」と読ませるので、console が起動したジョブの終了と突き合わせる。
    分からないものは running のまま（根拠の無い run を勝手に中断にしない）。"""
    if s.get("status") != "running": return s
    name, task, mt = s["name"], s.get("task"), s.get("mtime")
    cands = [j for j in jobs if j.get("kind") == "kb-run" and j.get("run_hint") == name]
    if not cands and task:   # run_hint を持たない古いジョブ: チケットが同じで、run の開始より前に始まっていないもの
        cands = [j for j in jobs if j.get("kind") == "kb-run" and not j.get("run_hint")
                 and str(j.get("ticket")) == str(task) and not after(s.get("started"), j.get("started"))]
    if cands:
        j = cands[0]                                            # JobStore.list() は開始の新しい順
        # state.json がジョブの終了より後に書かれていれば、別の runner が続きを回している（--resume 等）
        if j.get("state") != "running" and j.get("finished") and not after(mt, j["finished"]):
            s["status"] = "abandoned"
            s["runner"] = {"id": j["id"], "label": j.get("label"), "state": j.get("state"), "rc": j.get("rc"), "finished": j.get("finished")}
        return s
    t = tickets.get(name)   # ジョブの記録が無い run: 台帳が結果を反映済み（人間待ち）なら runner は終わっている
    if t and t.get("status") == "blocked" and after(t.get("updated"), mt): s["status"] = "abandoned"
    return s


def list_runs():
    out = []
    if not RUNS.exists(): return out
    for p in RUNS.iterdir():
        if p.is_dir() and not p.name.startswith("."): out.append(run_summary(p))
        elif p.is_file() and p.suffix == ".md" and p.name.startswith("20"): out.append(v0_summary(p))
    out.sort(key=lambda r: (r.get("started") or r.get("mtime") or ""), reverse=True)
    return apply_liveness(out)


def apply_liveness(runs):
    """実行中に見える run にだけ生死の判定をかける（ジョブと台帳を読むのは 1 件でもあるときだけ）"""
    live = [r for r in runs if r.get("status") == "running"]
    if not live: return runs
    jobs = JobStore.list()
    tickets = {}
    for t in rows("SELECT id, status, run, updated FROM tickets WHERE run IS NOT NULL AND run != ''"):
        tickets[str(t["run"]).rsplit("/", 1)[-1]] = t
    for r in live: run_liveness(r, jobs, tickets)
    return runs


# ---------- 実行記録の要約（ADR-0025: 停止の理由は表示側で導く。runner の state.json は変えない）
GATE_FAIL = re.compile(r"^FAIL (\S+)")
STEP_LOG = re.compile(r"^(agent|code)-(.+)-(\d+)\.log$")
WORK_DOC = re.compile(r"^work/[^/]+\.(md|txt)$")


def last_line(text, n=120):
    """自由文のエラーから、画面の 1 行に出す要約（最後の空でない行）。長い行は切る"""
    ls = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return ls[-1][:n] if ls else None


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
    o = {"reason": "unknown", "stopped_step": None, "stopped_index": None, "detail_file": None, "human": None, "merged": None,
         "gate_fails": [], "fail_count": 0, "loops_hit": False, "job": None, "error_summary": None, "pr_url": s.get("pr_url"),
         "resume": s.get("resume")}   # 続きから回すコマンド（human で止まり wip が残っている run だけ。333）
    if s.get("kind") == "v0": o["reason"] = "v0"; return o
    if s.get("status") == "not_started": o["reason"] = "not_started"; return o
    state = state or {}
    hist = state.get("history") or []
    # runner が居なくなった run（チケット 236）: 待っても進まないことと、終わったジョブを先に言う
    if s.get("status") == "abandoned":
        o["reason"] = "runner_gone"; o["job"] = s.get("runner")
        o["stopped_step"] = (state.get("current") or {}).get("step") or state.get("next")
        return o
    if s.get("status") == "running":
        o["reason"] = "running"; o["stopped_step"] = state.get("next"); return o
    # 工程が 1 つも始まらないまま失敗した run（take / checkout の失敗）は「記録にありません」ではなく準備段階の失敗
    if not hist and s.get("finished") and state.get("result") == "failed":
        o["reason"] = "failed_before_start"; o["stopped_step"] = (state.get("current") or {}).get("step") or "take"
        o["error_summary"] = last_line(state.get("error"))
        # VM の空きを待ったが出なかった run（チケット 242）。直す所は無く、チケットは未着手に戻っている
        if state.get("failure") == "wait_timeout":
            o["reason"] = "wait_timeout"; o["stopped_step"] = "wait-vm"; o["waited_s"] = int(state.get("waited_s") or 0)
        # 貸出直後の準備（project.yml の prepare）で落ちた run（チケット 330）。VM は取れていて、直す所は
        # prepare.sh か VM の側にある。「VM を取得できなかった」と混ぜない
        if state.get("failure") == "prepare":
            o["reason"] = "prepare_failed"; o["stopped_step"] = "prepare"
        return o
    # 人間が後始末（wip から PR を作ってマージ・打ち切り）をした run（チケット 335）。runner が確定した result より後の事実なので、
    # 止まった工程の話より先に言う。kb が state.json に足した `human` だけが根拠で、ここでは何も推し量らない（ADR-0039）
    human = state.get("human")
    if isinstance(human, dict) and s.get("finished"):
        o["reason"] = "human_abandoned" if human.get("result") == "abandoned" else "human_done"
        o["human"] = human; o["pr_url"] = human.get("pr_url") or o["pr_url"]
        o["resume"] = None                     # 片が付いた run に「続きから回す」は出さない
        return o
    if state.get("_error") or (not hist and s.get("finished")): return o
    if not hist: return o
    # runner が条件（ゲート緑・レビュー PASS・CI 緑）を確かめて自分でマージした run（ADR-0042）。
    # 根拠は runner が書いた `merged` だけで、PR ができた話より先に言う（もう人間の出番は無い）
    merged = state.get("merged")
    if isinstance(merged, dict) and merged.get("at") and s.get("finished"):
        o["reason"] = "merged"; o["merged"] = merged
        o["pr_url"] = merged.get("pr_url") or o["pr_url"]
        o["resume"] = None                     # 片が付いた run に「続きから回す」は出さない
        return o
    if s.get("pr_url"):
        o["reason"] = "pr_created"
        # 自動マージまで行って、条件を満たさず開いたまま人間に渡った run。理由は runner が error に 1 行で残している
        if any(h.get("step") == "automerge" and h.get("ok") is False for h in hist):
            o["automerge_error"] = last_line(state.get("error"))
        return o
    last = hist[-1]
    if last.get("ok") is False:
        step = last.get("step"); o["stopped_step"] = step; o["stopped_index"] = len(hist) - 1
        o["fail_count"] = sum(1 for e in hist if e.get("step") == step and e.get("ok") is False)
        sd = next((x for x in (wf or {}).get("steps") or [] if x.get("id") == step), {})
        on_fail = sd.get("on_fail")
        if isinstance(on_fail, dict) and on_fail.get("goto"):
            o["loops_hit"] = (state.get("loops") or {}).get(f"{step}->{on_fail['goto']}", 0) >= on_fail.get("max_loops", 1)
        o["reason"] = "loop_limit" if o["loops_hit"] else "step_failed"
        # 時間上限で切られた工程（チケット 329）。「工程が失敗した」とは直し方が違う（上限を上げるか、チケットを小さくする）ので分ける。
        # コミット済みの分は wip ブランチに残っているので、次の実行は続きから進められる
        if last.get("failure") == "timeout":
            o["reason"] = "step_timeout"; o["timeout_min"] = last.get("timeout_min")
            o["error_summary"] = last_line(state.get("error"))
        # 鍵の利用枠の上限（quota）/ 鍵そのもの（key）で止まった工程（チケット 380 / ADR-0043）。工程の失敗ではない。
        # quota はチケットが todo に戻っていて、解除時刻（retry_after）の後に timer が続きを回す。key は人が鍵を直す
        if last.get("failure") in ("quota", "key"):
            o["reason"] = "quota_paused" if last["failure"] == "quota" else "key_failed"
            o["quota_type"] = state.get("quota_type") or last.get("quota_type")
            o["retry_after"] = ts_aware(state.get("retry_after") or last.get("retry_after"))
            o["quota_hits"] = int(state.get("quota_hits") or 0)
            o["quota_max_hits"] = int(os.environ.get("AIFACTORY_RESUME_MAX_HITS") or 6)   # kb と同じ上限（超えたら自動再開は止まっている）
            o["error_summary"] = last_line(state.get("error"))
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
    apply_liveness([s])                                         # 一覧と同じ規則で「実行中」を見直す（チケット 236）
    # このコンソールから起動したジョブ
    jobs = [j for j in JobStore.list() if j.get("run_hint") == name or (j.get("ticket") and s.get("task") and str(j["ticket"]) == str(s["task"]))]
    try: outcome = run_outcome(d, s, state, wf, files)
    except Exception as e: outcome = {"reason": "unknown", "stopped_step": None, "stopped_index": None, "detail_file": None,
                                      "gate_fails": [], "fail_count": 0, "loops_hit": False, "pr_url": s.get("pr_url"), "_error": str(e)}
    return {"summary": s, "state": state, "files": files, "workflow": wf, "ticket": ticket, "jobs": jobs[:5],
            "lease": run_lease(s.get("task")), "outcome": outcome, "groups": run_groups(files, wf)}


def run_lease(task):
    """この run のチケットに VM が貸し出されたままか。止まった run から返却の確認先へ導くために添える"""
    if not task or not SANDBOX_STATE.exists(): return None
    try: lent = json.loads(SANDBOX_STATE.read_text(encoding="utf-8"))
    except Exception: return None
    v = lent.get(str(task))
    # since は API が返す時刻なのでオフセットを補う（ADR-0026）
    return {"task": str(task), "pj": v.get("pj"), "name": v.get("name"), "since": ts_aware(v.get("since"))} if isinstance(v, dict) else None


def rel(p):
    """read_file に渡せるパス。リポジトリ内なら相対、外（workspace を外に置いた場合）なら絶対"""
    p = pathlib.Path(p).resolve()
    return str(p.relative_to(REPO.resolve())) if p.is_relative_to(REPO.resolve()) else str(p)


def read_file(relpath, tail=None, offset=None):
    p = (REPO / relpath).resolve()   # 絶対パスならそのまま（workspace がリポジトリ外でもよい。根の検査は下）
    if not any(p.is_relative_to(r.resolve()) for r in READ_ROOTS if r.exists()): return None, "この場所のファイルは表示できません（読めるのは runs / kanban/tickets / logs / workflow/kit / PJ 定義 / console/jobs の下だけです）"
    if not p.is_file(): return None, "ファイルが見つかりません"
    size = p.stat().st_size
    if attachments.is_image(p.name):   # 画像はテキストにせず base64 で返す（MCP は image ブロック、画面は data: URL にする）
        if size > READ_IMAGE_MAX:
            return None, (f"画像が大きすぎて読めません（{attachments.human(size)}、上限 {attachments.human(READ_IMAGE_MAX)}）。"
                          "コンソールの添付から開いて見てください")
        return {"path": relpath, "size": size, "type": attachments.guess_type(p.name),
                "base64": base64.b64encode(p.read_bytes()).decode("ascii"), "truncated": False}, None
    with open(p, "rb") as f:
        if offset is not None:
            f.seek(min(offset, size)); data = f.read()
            return {"path": relpath, "size": size, "offset": min(offset, size), "text": data.decode("utf-8", "replace"), "truncated": False}, None
        if tail and size > tail:
            f.seek(size - tail); data = f.read()
            return {"path": relpath, "size": size, "text": data.decode("utf-8", "replace"), "truncated": True}, None
        return {"path": relpath, "size": size, "text": f.read().decode("utf-8", "replace"), "truncated": False}, None


# ---------- sandbox
def env_has_claude_key(path):
    """env ファイルに無印の Claude の鍵（CLAUDE_CODE_OAUTH_TOKEN=…）が入っているか。有無だけを見て値は持たない"""
    try: return any(re.match(r"^CLAUDE_CODE_OAUTH_TOKEN=\S", l.strip()) for l in pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError: return False


def key_pool_counts():
    """鍵プールのうち有効な鍵の数（用途ごと）。sandbox 画面が「この PJ の鍵はどこから来るか」を言うのに使う（ADR-0045）"""
    ks = [k for k in keys_view().get("keys") or [] if k.get("enabled")]
    return {"fable": sum(1 for k in ks if k["allow"].get("fable")), "other": sum(1 for k in ks if k["allow"].get("other")), "total": len(ks)}


def key_source_for(pj, pool):
    """この PJ の VM に渡る Claude の鍵の出どころ。pool（両用途ともプール）/ pool_partial（片方だけプール、残りは env）/
    pj（PJ 別 env。非推奨）/ global（全体の env）/ none。値は見ない（有無だけ）"""
    if pool["fable"] and pool["other"]: return "pool"
    fallback = "pj" if env_has_claude_key(SANDBOX_PJ_DIR / f"{pj}.env") else "global" if env_has_claude_key(SANDBOX_STATE.parent / "env") else "none"
    if pool["fable"] or pool["other"]: return "pool_partial" if fallback != "none" else "pool_partial_nofallback"
    return fallback


def sandbox_env():
    """~/.config/sandbox/env から表示に要る 2 項目だけ読む（トークン等の他の行は読まない）"""
    out = {"SB_DOMAIN": "sb.internal", "APP_PORT": "3000"}
    p = SANDBOX_STATE.parent / "env"
    if p.exists():
        for l in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^(SB_DOMAIN|APP_PORT)=\"?([^\"#\s]+)", l.strip())
            if m: out[m.group(1)] = m.group(2)
    return out


VM_NAME = re.compile(r"^sb-(?P<mid>.+)-\d+$")   # sb-<t>-<pj>-NN（ADR-0017）と旧命名 sb-<pj>-NN の両方
TENANT = re.compile(r"^[a-z0-9]{1,6}$")         # テナントの slug（sandbox/proxmox/_tenant.sh）


def pj_of_vm(name, known):
    """VM 名から PJ を引く。既知の PJ（known）に当たったものだけ返し、当たらなければ None。

    テナント部分は任意（`sb-main-aifactory-01` も `sb-kumitate-01` も数える）。PJ 名にハイフンがあってもよいように
    長い一致を優先する。`-base` / `-gw` / `-ctl` / `-tpl-` は CLI の ls が既に除いている。
    """
    m = VM_NAME.match(name or "")
    if not m: return None
    mid = m.group("mid")
    hit = [p for p in known if mid == p or (mid.endswith("-" + p) and TENANT.match(mid[:-len(p) - 1]))]
    return max(hit, key=len) if hit else None


def parse_ls(text, known=None):
    """`sandbox ls` のジョブログを VM 1 台 = 1 件の dict に分ける。

    CLI は固定幅の printf で出す（sandbox/bin/sandbox の cmd_ls）。VM 名・IP・時刻に空白は入らないので空白で区切る。
    捨てる行: ジョブ先頭の `$ ...`、`[error]` のような注記、見出し（TASK ...）、列数が合わない行。
    読めない行は黙って捨てる（生ログはジョブの記録にそのまま残る）。task が `-`（貸出なし）のときは None。
    同じ VM に複数の貸出があると CLI は 1 台 1 行のまま task を `221,222` と並べるので、戻りの task もカンマ区切りになる。
    pj は VM 名から引く（known を渡さなければ既知の PJ すべて）。当たらなければ None。
    """
    known = pjs() if known is None else known
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s[0] in "$[": continue
        f = s.split()
        if f[0] == "TASK" or not 5 <= len(f) <= 6: continue
        task, name, vmid, ip, status = f[:5]
        out.append({"task": None if task == "-" else task, "name": name, "vmid": vmid, "ip": ip,
                    "status": status, "since": f[5] if len(f) == 6 else None, "pj": pj_of_vm(name, known)})
    return out


def _task_key(t):
    """チケット番号は数として並べる（10 が 9 の前に来ない）。番号でなければ後ろにまとめる"""
    return (0, int(t), "") if str(t).isdigit() else (1, 0, str(t))


def leases_by_vmid(lent):
    """台帳を vmid ごとにまとめる → {"9213": ["221", "222"]}。vmid は str に揃える（台帳は数値、`sandbox ls` は文字列）"""
    by = {}
    for task, v in (lent or {}).items():
        if not isinstance(v, dict) or v.get("vmid") in (None, ""): continue
        by.setdefault(str(v["vmid"]), []).append(str(task))
    return {vmid: sorted(tasks, key=_task_key) for vmid, tasks in by.items()}


def idle_stop_view():
    """~/.config/sandbox/idle-stop.json（sandbox CLI の idle-stop が書く）。無ければ None。

    「稼働状態が停止中」の理由が節電なのか壊れているのかは、この一覧に vmid があるかどうかでしか分からない。
    読むだけで、止める・起こすはコンソールからはしない（次の貸出が起こす）。
    """
    p = SANDBOX_STATE.parent / "idle-stop.json"
    if not p.exists(): return None
    try: d = json.loads(p.read_text(encoding="utf-8"))
    except Exception: return None
    if not isinstance(d, dict): return None
    stopped = [v for v in (d.get("stopped") or []) if isinstance(v, dict)]
    # candidates = 最終利用から hours 経ったが、足切り（keep 台）の内なので起動したまま残している VM（2026-09-09）
    candidates = [v for v in (d.get("candidates") or []) if isinstance(v, dict)]
    return {"hours": d.get("hours"), "keep": d.get("keep"),
            "last_run": ts_aware(d.get("last_run")) if d.get("last_run") else None,
            "stopped": [{"vmid": str(v.get("vmid")), "name": v.get("name"),
                         "at": ts_aware(v["at"]) if v.get("at") else None, "last_used": v.get("last_used")} for v in stopped],
            "candidates": [{"vmid": str(v.get("vmid")), "name": v.get("name"), "last_used": v.get("last_used")} for v in candidates]}


def recent_red_gates(pj, limit=20):
    """直近の run が「base でも赤い」と実際に確かめたゲート名（workflow/bin/run の note_base_red が state.json に書く。330）。

    project.yml の known_red_gates は人が手で書くもので、実際は誰も書かなかった。機械が確かめた分をここで拾って
    合わせて見せる。run 名は <日付>-<pj>-<チケット> なので、名前の降順＝新しい順に数件だけ読む"""
    out = []
    if not RUNS.is_dir(): return out
    names = sorted((p.name for p in RUNS.iterdir() if p.is_dir() and f"-{pj}-" in p.name), reverse=True)[:limit]
    for n in names:
        try: s = json.loads((RUNS / n / "state.json").read_text(encoding="utf-8"))
        except Exception: continue
        for g in s.get("known_red_gates") or []:
            if g not in out: out.append(g)
    return out


def sandbox_view():
    lent, state_error = {}, None
    state_exists = SANDBOX_STATE.exists()
    if state_exists:
        try: lent = {k: ts_keys(v, "since") for k, v in json.loads(SANDBOX_STATE.read_text(encoding="utf-8")).items()}
        except Exception as e: lent = {"_error": str(e)}; state_error = str(e)
    else:
        state_error = f"{SANDBOX_STATE} がありません"   # 「貸出なし」と「台帳を読めていない」を呼び手が区別できるようにする（336）
    # 貸出は「1 チケット = 1 件」、VM は vmid の異なり。同じ VM を 2 台と数えないために分けて持つ（チケット 237）
    by_vmid = leases_by_vmid(lent)
    shared = {vmid: tasks for vmid, tasks in by_vmid.items() if len(tasks) > 1}
    known = pjs()
    ls_jobs = [j for j in JobStore.list() if j.get("kind") == "sandbox-ls"]   # 新しい順
    last_ls = ls_jobs[0] if ls_jobs else None                                 # 直近（失敗・実行中も含む）。画面は取得中 / 成功 / 失敗 / 未取得を分けて出す
    last_ok_ls = next((j for j in ls_jobs if j.get("rc") == 0), None)          # 表に出せる最後の成功。失敗しても前回の表は残す
    vms = []
    if last_ok_ls:
        log = JOBS / last_ok_ls["id"] / "log"
        if log.exists(): vms = parse_ls(log.read_text(encoding="utf-8", errors="replace"), known)
    # 「実体」は最後に成功した ls の時点の台数。ls を一度も取れていなければ数を作らない（0 台と言い切らない）
    actual = {pj: sum(1 for v in vms if v["pj"] == pj) for pj in known} if last_ok_ls else {}
    tpl = []
    pool = key_pool_counts()
    for pj in known:
        py = project_yml(pj)
        y = load_yaml(py) if py.exists() else None
        mine = {t: v for t, v in lent.items() if isinstance(v, dict) and v.get("pj") == pj}
        n_lent = len(leases_by_vmid(mine))
        n_actual = actual.get(pj)                     # ls が無ければ None（未取得）
        # 実体と貸出は取得の時点が違う（ls に出ない VM が台帳にあることもある）。空きは 0 で止める
        free = max(n_actual - n_lent, 0) if n_actual is not None else None
        unbuilt = max(POOL_PER_PJ - n_actual, 0) if n_actual is not None else None
        red_gates = list((y or {}).get("known_red_gates") or [])
        red_gates += [g for g in recent_red_gates(pj) if g not in red_gates]
        tpl.append({"pj": pj, "project_yml": py.exists(), "repo": (y or {}).get("repo"), "base_branch": (y or {}).get("base_branch"),
                    "display_name": (y or {}).get("display_name", pj), "token_file": (SANDBOX_PJ_DIR / f"{pj}.env").exists(),
                    "key_source": key_source_for(pj, pool),   # VM に渡る Claude の鍵の出どころ（ADR-0045）
                    "lent": n_lent, "leases": len(mine),   # 使用数は台数。件数は共有のときだけ画面に添える
                    "pool_defined": POOL_PER_PJ, "pool_actual": n_actual, "free": free, "unbuilt": unbuilt,
                    "hint": f"未構築 {unbuilt} 台。proxmox/40-pool.sh {pj} {unbuilt} で足せます" if unbuilt else None,
                    # 人が project.yml に書いた分と、runner が base で回して確かめた分（330）を合わせて見せる
                    "pool": POOL_PER_PJ, "known_red_gates": red_gates})
    fetched = ts_aware(last_ok_ls["finished"]) if last_ok_ls and last_ok_ls.get("finished") else None
    age = None
    if fetched:
        try: age = int((datetime.datetime.now().astimezone() - ts_dt(fetched)).total_seconds())
        except ValueError: age = None
    e = sandbox_env()
    urls = {t: f"http://task-{t}.{e['SB_DOMAIN']}:{e['APP_PORT']}" for t, v in lent.items() if isinstance(v, dict)}
    # 貸出 1 件 = 1 行の一覧（台帳の写し）。MCP から IP を引くのに state.json を ssh で直読みしなくて済むようにする（336）
    status_of = {str(v["vmid"]): v.get("status") for v in vms if v.get("vmid")}
    # keys は take / reinject が書いた鍵プールの名前（{"fable": …, "other": …}）。値は台帳にも入らない（ADR-0044）
    leases = [{"task": str(t), "vmid": None if v.get("vmid") in (None, "") else str(v["vmid"]), "name": v.get("name"),
               "ip": v.get("ip"), "pj": v.get("pj"), "since": v.get("since"), "phase": v.get("phase"),
               "url": urls.get(t), "vm_status": status_of.get(str(v.get("vmid"))),
               "keys": v.get("keys") if isinstance(v.get("keys"), dict) else None}
              for t, v in sorted(lent.items(), key=lambda kv: _task_key(kv[0])) if isinstance(v, dict)]
    return {"lent": lent, "leases": leases, "state_exists": state_exists, "state_error": state_error,
            "urls": urls, "templates": tpl, "pool_per_pj": POOL_PER_PJ, "state_file": str(SANDBOX_STATE), "key_pool": pool,
            "lease_count": sum(1 for v in lent.values() if isinstance(v, dict)), "vm_count": len(by_vmid), "shared": shared,
            "last_ls": last_ls, "last_ok_ls": last_ok_ls, "vms": vms,
            "ls_fetched": fetched, "ls_age_s": age, "ls_stale": bool(age is not None and age > LS_STALE_S),
            "idle_stop": idle_stop_view()}


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
    def start(cls, kind, cmd, label, ticket=None, stdin_text=None, run_hint=None, cwd=None, conflict=None, files=None):
        """conflict: 実行中ジョブ j を受けて衝突なら理由文字列を返す関数。ロックの中で判定するので同時リクエストでも二重にならない。
        files: [(名前, バイト列)]。<job>/files/ に落とし、cmd の "{files}" をそのパスの並びに置き換える（"{stdin}" と同じ流儀）"""
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
            if files:
                fd = d / "files"; fd.mkdir()
                saved = []
                for name, data in files:
                    fp = fd / attachments.free_name(fd, name); fp.write_bytes(data); saved.append(str(fp))
                cmd = [x for a in cmd for x in (saved if a == "{files}" else [a])]
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


# ---------- 枠組みの checkout（ctl の ~/aifactory）が origin と食い違っていないか
REPO_STATUS_TTL = 30            # overview は 5 秒ごとに来る。git は 30 秒に 1 回だけ呼ぶ
_repo_status_cache = {}


def _git_out(path, *args):
    """git を読むだけで呼ぶ。戻り: (rc, stdout)。git が無い・checkout でない・応答が無いときも例外にしない"""
    try:
        r = subprocess.run(["git", *args], cwd=str(path), text=True, capture_output=True, errors="replace", timeout=5)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return r.returncode, r.stdout.strip()


def repo_status(path=None, ttl=REPO_STATUS_TTL):
    """この checkout（runner と console が動いている枠組みそのもの）が origin と食い違っていないか（チケット 337）。

    runner は PJ 定義（examples/projects/<pj>/ の project.yml / gates.sh / provision.sh）を作業ツリーから
    直接読むので、ctl で直して push していない変更はそのまま本番の挙動になる。origin と食い違ったまま
    動いていることに気づけるように、ahead / behind / 汚れ を overview に添える。判定はここ 1 か所（ADR-0015）。

    - 網は触らない（fetch しない）。behind は最後に fetch した時点との差
    - git が無い・checkout でないときは known=False（分からない。警告も出さない）
    - 上流が無いとき（detached や追跡なし）は ahead / behind は None。detached=True で分かる
    """
    p = pathlib.Path(path or REPO)
    key = str(p)
    hit = _repo_status_cache.get(key)
    if hit and (time.monotonic() - hit[0]) < ttl: return hit[1]
    d = {"path": key, "known": False, "branch": None, "upstream": None, "detached": False,
         "ahead": None, "behind": None, "dirty": 0, "diverged": False}
    rc, top = _git_out(p, "rev-parse", "--show-toplevel")
    if rc == 0 and top:
        d["known"] = True
        _, branch = _git_out(p, "rev-parse", "--abbrev-ref", "HEAD")
        d["detached"] = branch == "HEAD"
        d["branch"] = None if d["detached"] else (branch or None)
        rc, up = _git_out(p, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
        if rc == 0 and up:
            d["upstream"] = up
            rc, counts = _git_out(p, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
            n = counts.split()
            if rc == 0 and len(n) == 2: d["ahead"], d["behind"] = int(n[0]), int(n[1])
        rc, dirty = _git_out(p, "status", "--porcelain")
        if rc == 0: d["dirty"] = len([l for l in dirty.splitlines() if l.strip()])
        d["diverged"] = bool(d["ahead"] or d["behind"] or d["dirty"])
    _repo_status_cache[key] = (time.monotonic(), d)
    return d


# ---------- overview
def overview(pj=None, limit=6):
    """概況。pj を渡すと run の一覧だけその PJ に絞る（上限を掛ける前に絞る。7 本以上動いていても選んだ PJ の run が漏れない）。
       counts はナビのバッジ用に常に全 PJ の集計（画面の PJ 選択と連動させない。console/UX.md のボード行）"""
    counts = {s: 0 for s in STATUSES}
    for r in rows("SELECT status, COUNT(*) n FROM tickets GROUP BY status"): counts[r["status"]] = r["n"]
    running = JobStore.running()
    runs = [r for r in list_runs() if r["kind"] == "v1" and not r.get("dry") and (not pj or r.get("pj") == pj)]
    active = [r for r in runs if r["status"] == "running"]
    not_started = [r for r in runs if r["status"] == "not_started"]
    abandoned = [r for r in runs if r["status"] == "abandoned"]
    lent = {}
    if SANDBOX_STATE.exists():
        try: lent = json.loads(SANDBOX_STATE.read_text(encoding="utf-8"))
        except Exception: lent = {}
    return {"counts": counts, "labels": STATUS_LABEL, "jobs_running": len(running), "jobs": running[:limit],
            "pj": pj or None, "limit": limit,
            "runs_active": active[:limit], "runs_active_n": len(active),          # 一覧は上限つき、件数は絞り込み後の全件（画面が「ほか n 件」を出す）
            "runs_not_started": {"n": len(not_started), "runs": not_started[:limit]},
            "runs_abandoned": {"n": len(abandoned), "runs": abandoned[:limit]},
            "lent": len([v for v in lent.values() if isinstance(v, dict)]),      # 貸出の件数（MCP の既存利用者のために残す）
            "vms_lent": len(leases_by_vmid(lent)),                                # ナビに出す台数（同じ VM の 2 件は 1 台）
            "db": DB.exists(), "kb_root": str(KB_ROOT), "paths": paths.describe(),
            "repo": repo_status(),                                                # PJ 定義を読む checkout が origin と食い違っていないか（337）
            "now": now(), "tz": tz_info()}




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
            "attachments": [{**a, "path": rel(attachments.dir_for(tid) / a["name"]), "image": attachments.is_image(a["name"])}
                            for a in attachments.listing(tid)],
            "history": hist, "runs": runs, "jobs": jobs, "kinds": ks, "kind_desc": kind_desc(ks), "labels": STATUS_LABEL, "project_yml": py.exists()}


def ticket_action(tid, b):
    act = b.get("action"); stdin = None
    if act in ("start", "review", "done", "reopen", "block"):
        if act == "block" and not b.get("note"): raise ApiError("人間待ちにするには、何を待っているかを note に書いてください")
        args = [act, tid] + (["--note", b["note"]] if b.get("note") else [])
    elif act == "set":
        args = ["set", tid]
        # note だけは「キーが無い＝触らない / 空文字列＝消す」。他は空を未指定として無視する（kb が die するため）
        if "note" in b and b["note"] is not None: args += ["--note", b["note"]]
        for k in ("status", "pr", "kind"):
            if b.get(k) not in (None, ""): args += [f"--{k}", b[k]]
        if b.get("run"): args += ["--run", b["run"]]
        if len(args) == 2: raise ApiError("変える項目がありません。status / pr / note / kind / run のどれかを指定してください")
    elif act == "append":
        text = b.get("text")
        if not text or not str(text).strip(): raise ApiError("追記する本文がありません。text に本文を入れてください")
        args = ["append", tid] + (["--section", b["section"]] if b.get("section") else [])
        stdin = str(text)
    elif act == "sync":
        return sync_apply(tid, b)
    else: raise ApiError(f"操作 {act} はありません。start / review / done / reopen / block / set / append / sync のどれかを指定してください")
    rc, out, err = kb(*args, stdin=stdin)
    if rc != 0: raise ApiError((err or out).strip() or f"kb {act} が失敗 rc={rc}")
    return {"rc": rc, "stdout": out, "stderr": err}


def run_action(name, b):
    """実行記録に人間の後始末を書く（kb run-note。チケット 335）。runner が確定した result は変えず `human` を足すだけで、
       「人間が PR#n で仕上げた」という言い方は画面側が導く（ADR-0025 / ADR-0039）。
       close は決着を初めて記録する（既に記録がある run は kb が断る）。note は書いた説明を直す（決着の別はそのまま）"""
    act = b.get("action") or "close"
    d = RUNS / name
    if not d.is_dir() or not d.resolve().is_relative_to(RUNS.resolve()): raise ApiError(f"実行記録 {name} は見つかりません", 404)
    text = b.get("text")
    if act == "close":
        result = b.get("result") or "done"
        if result not in ("done", "abandoned"): raise ApiError("result は done（人間が仕上げた）か abandoned（打ち切った）のどちらかです")
        args = ["run-note", name, "--result", result]
    elif act == "note":
        if not text or not str(text).strip(): raise ApiError("書き残す説明がありません。text に本文を入れてください")
        args = ["run-note", name, "--force"]      # 決着の別と PR は、渡さなければ前の記録のまま
    else: raise ApiError(f"操作 {act} はありません。close / note のどちらかを指定してください")
    if b.get("pr") not in (None, ""): args += ["--pr", b["pr"]]
    if text not in (None, ""): args += ["--text", str(text)]
    rc, out, err = kb(*args)
    if rc != 0: raise ApiError((err or out).strip() or f"kb run-note が失敗 rc={rc}")
    return {"rc": rc, "stdout": out, "stderr": err}


def sync_apply(tid, b):
    """「実行記録に状態を合わせる」。状態とメモを上書きする半可逆の操作なので、既定は書かずに前後を返す（下見）。
       書くのは dry_run に false を明示したときだけ。画面はダイアログで確認してから明示し、MCP は呼び手が明示する。
       文字列の "false" は下見のまま扱う（安全側。書くのは JSON の false だけ）"""
    dry = b.get("dry_run")
    dry = True if dry is None else bool(dry)
    p = sync_preview(tid, b.get("run"))
    out = err = None
    if not dry:
        rc, out, err = kb("sync", str(tid), "--run", p["run"])
        if rc != 0: raise ApiError((err or out).strip() or f"kb sync が失敗 rc={rc}")
    warn = []
    if p.get("updated_after_run"):
        warn.append(f"この run が終わった後（{p['ticket']['updated']}）にチケットが更新されています。"
                    + ("実行すると、その更新を上書きします。" if dry else "その更新を上書きしました。"))
    if dry: warn.append("まだ書き込んでいません。書くには dry_run に false を指定してください。")
    return {**p, "dry_run": dry, "warning": " ".join(warn) or None, "stdout": out, "stderr": err}


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
    if b.get("wait"): cmd += ["--wait", str(int(b["wait"]))]   # VM の空き待ちの上限（分。242）
    # human で止まった run を新しい VM で続きから（333）。step を省くと runner が記録の resume_step を使う
    fs = b.get("from_step")
    if fs is not None: cmd.append(f"--from={fs}" if fs else "--from")
    if b.get("from_branch"): cmd.append(f"--branch={b['from_branch']}")
    label = f"kb run {tid}" + (" --dry-run" if b.get("dry_run") else "") + (" --resume" if b.get("resume") else "") \
        + ((" --from " + fs) if fs else (" --from" if fs is not None else "")) + (f" --branch {b['from_branch']}" if b.get("from_branch") else "")
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


# ---------- 添付（正本は lib/aifactory_attachments.py。ここは入口で、書くのは必ず kb 経由＝ history と updated が揃う）
def ticket_attach(tid, files):
    """files=[(名前, バイト列)] をチケットに添付する。一時ファイルに落として kb attach を 1 件ずつ呼ぶ。
       名前の締め（長さ・文字種）も大きさの上限も lib の判定をそのまま使う（数値をここに書かない）"""
    if not files: raise ApiError("添付するファイルがありません")
    names = lambda: [a["name"] for a in attachments.listing(tid)]
    added, before = [], names()
    with tempfile.TemporaryDirectory() as td:
        for name, data in files:
            p = pathlib.Path(td) / attachments.free_name(td, name)
            p.write_bytes(data)
            rc, out, err = kb("attach", tid, str(p))
            # 入った名前は kb の出力を字句解析せず一覧の差分で取る（名前に空白があっても切れない。`-2` の付け替えも拾える）
            after = names()
            added += [n for n in after if n not in before]
            before = after
            if rc != 0:
                msg = (err or out).strip() or f"kb attach が失敗 rc={rc}"
                raise ApiError((f"{len(added)} 件（{'、'.join(added)}）は添付できました。" if added else "") + msg)
    return {"id": int(tid), "added": added, "attachments": attachments.listing(tid)}


def ticket_attach_path(tid, path):
    """ctl の上にあるファイルを添付する（MCP の path 用）。読んでよいのはホームか /tmp の下で、
       `.` で始まる要素を含まないものだけ（ctl.env・.ssh のような秘密の置き場をこの規則で弾く）"""
    p = pathlib.Path(str(path)).expanduser()
    if not p.is_absolute(): raise ApiError("path は絶対パスで指定してください（例 /tmp/画面.png）")
    p = p.resolve()
    if any(x.startswith(".") for x in p.parts):
        raise ApiError("`.` で始まる名前を含むパスは添付できません（設定や鍵の置き場を避けるためです）")
    if not any(p.is_relative_to(r.resolve()) for r in ATTACH_PATH_ROOTS if r.exists()):
        raise ApiError("このパスは添付できません（添付できるのはホームディレクトリか /tmp の下だけです）")
    if not p.is_file(): raise ApiError(f"ファイルが見つかりません: {p}", 404)
    return ticket_attach(tid, [(p.name, p.read_bytes())])


def ticket_detach(tid, name):
    """添付を 1 件消す（kb detach）。history に残る"""
    if not name: raise ApiError("消す添付の名前を指定してください")
    rc, out, err = kb("detach", tid, name)
    if rc != 0: raise ApiError((err or out).strip() or f"kb detach が失敗 rc={rc}", 404)
    return {"id": int(tid), "removed": name, "attachments": attachments.listing(tid)}


def attachment_file(tid, name):
    """添付 1 件を配信するための (パス, Content-Type)。置き場の外を指す名前・無い名前は None（呼び元が 404）"""
    p = attachments.path_of(tid, name)
    return (p, attachments.guess_type(p.name)) if p else None


def op_intake(b, files=None):
    """自由文（と添付）から起票する。files があれば <job>/files/ に落として intake の --attach に渡す"""
    text = (b.get("text") or "").strip()
    if not text: raise ApiError("依頼文が空です。取り込む文章を text に入れてください")
    cmd = [str(REPO / "glue" / "bin" / "intake"), "{stdin}"]
    if b.get("pj"): cmd += ["--pj", b["pj"]]
    if b.get("kind"): cmd += ["--kind", b["kind"]]
    if b.get("dry_run"): cmd.append("--dry-run")
    if files: cmd += ["--attach", "{files}"]
    label = "intake" + (" --dry-run" if b.get("dry_run") else "") + f"（{text[:30]}…）" + (f"添付 {len(files)} 件" if files else "")
    return {"job": JobStore.start("intake", cmd, label, stdin_text=text + "\n", files=files)}


def op_dispatch(b):
    cmd = [str(REPO / "glue" / "bin" / "dispatch")]
    if b.get("pj"): cmd += ["--pj", b["pj"]]
    if b.get("once"): cmd.append("--once")
    elif b.get("max"): cmd += ["--max", str(int(b["max"]))]
    if b.get("dry_run"): cmd.append("--dry-run")
    if b.get("wait"): cmd += ["--wait", str(int(b["wait"]))]   # VM の空き待ちの上限（分。242）
    serial = (lambda j: None) if b.get("dry_run") else (lambda j: f"dispatch（ジョブ {j['id']}）が既に実行中です。直列で回す約束なので、終わるまで待ってください" if j.get("kind") == "dispatch" else None)
    return {"job": JobStore.start("dispatch", cmd, "dispatch " + " ".join(cmd[1:]), conflict=serial)}


def op_sandbox_ls(conflict=None):
    return {"job": JobStore.start("sandbox-ls", ["sandbox", "ls"], "sandbox ls", conflict=conflict)}


def sandbox_ls_refresh_if_stale(view):
    """`sandbox ls` の値が古ければ、裏で取り直しのジョブを起こして view に印を付けて返す（チケット 336・ADR-0038）。

    Proxmox への ssh に数秒かかるので、その場で待たずに「今回は古い値 + ls_refreshing: true」を返す。次の呼び出しで最新になる。
    読み取りのツールが状態（jobs/）を増やす唯一の例外なので、起こす条件を 3 つに絞る:
      - 値が古い（LS_STALE_S 超）か、まだ一度も取れていない
      - sandbox-ls が実行中でない（実行中ならその id を返すだけ）
      - 直近の sandbox-ls（成否問わず）の開始から LS_STALE_S 秒より経っている（失敗を叩き続けない）
    起こせないときは view["ls_refresh_error"] に理由を入れる。sandbox_status 自体は失敗させない（読めた分は返す）。
    view はその場で書き換えて返す。
    """
    view["ls_refreshing"] = False
    if not (view.get("ls_stale") or view.get("ls_fetched") is None): return view
    jobs = [j for j in JobStore.list() if j.get("kind") == "sandbox-ls"]     # 新しい順
    running = next((j for j in jobs if j.get("rc") is None and j.get("state") == "running"), None)
    if running:
        view.update({"ls_refreshing": True, "ls_refresh_job": running["id"]}); return view
    last = jobs[0] if jobs else None
    if last and last.get("started") and not after(now(), _shift(last["started"], LS_STALE_S)):
        view["ls_refresh_error"] = f"直近の sandbox ls（{last['id']}）から {LS_STALE_S} 秒経っていないので取り直しません"
        return view
    if not shutil.which("sandbox", path=child_env().get("PATH")):
        view["ls_refresh_error"] = "sandbox コマンドが PATH にありません"; return view
    try:
        job = op_sandbox_ls(conflict=lambda j: "sandbox ls が実行中" if j.get("kind") == "sandbox-ls" else None)["job"]
        view.update({"ls_refreshing": True, "ls_refresh_job": job["id"]})
    except Conflict:
        view["ls_refreshing"] = True                                         # 別の口が同時に起こした。それの結果を待てばよい
    except Exception as e:
        view["ls_refresh_error"] = f"{type(e).__name__}: {e}"
    return view


def _shift(ts, seconds):
    """ISO 8601 の時刻を seconds 秒ずらした文字列。読めなければ元のまま返す"""
    try: return (ts_dt(ts) + datetime.timedelta(seconds=seconds)).isoformat(timespec="seconds")
    except (TypeError, ValueError): return ts


def op_sandbox_release(b):
    task = str(b.get("task") or "")
    if not re.match(r"^\d{3,}$", task): raise ApiError("チケット番号（task）は 3 桁以上の数字で指定してください")
    busy = lambda j: f"チケット {task} のジョブ {j['id']} が実行中です。先にジョブを止めてから返却してください" if j.get("ticket") == int(task) else None
    return {"job": JobStore.start("sandbox-release", ["sandbox", "release", task], f"sandbox release {task}", ticket=int(task), conflict=busy)}


# ---------- Claude の鍵プール（keys.json。ADR-0044）
# 読むのは名前・フラグ・末尾 4 文字だけ。token の値は keys_view にも応答にも例外文にも入れない。
# 書くのは必ず sandbox CLI 経由（core が keys.json を直接書くことはない。state.json と同じ約束）
KEY_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")


def sandbox_cli(args, stdin=None):
    """sandbox CLI を同期で呼ぶ（数秒で終わる操作だけ）。戻り: (rc, stdout, stderr)。

    鍵の追加・差し替えは JobStore に載せない: JobStore は stdin を jobs/<id>/stdin.txt に、コマンド行を log に書くので、
    トークンが記録に残ってしまう。ここでは stdin をパイプで渡すだけにする（記録に残らない）"""
    if not shutil.which("sandbox", path=child_env().get("PATH")): raise ApiError("sandbox コマンドが PATH にありません")
    r = subprocess.run(["sandbox", *map(str, args)], text=True, capture_output=True, errors="replace",
                       input=stdin, env=child_env(), cwd=str(REPO))
    return r.returncode, r.stdout, r.stderr


def keys_view():
    """鍵プールの一覧（マスク済み）。keys.json を直接読む（state.json と同じ流儀）。token の値は返さない"""
    path = SANDBOX_KEYS
    raw, error = [], None
    exists = path.exists()
    if exists:
        try: raw = json.loads(path.read_text(encoding="utf-8")).get("keys") or []
        except Exception as e: error = str(e)
    lent = {}
    if SANDBOX_STATE.exists():
        try: lent = json.loads(SANDBOX_STATE.read_text(encoding="utf-8"))
        except Exception: lent = {}

    def in_use(name):
        """その鍵を使っている貸出中のチケット（take / reinject が state.json の keys に残した名前）"""
        ts = [str(t) for t, v in lent.items() if isinstance(v, dict) and isinstance(v.get("keys"), dict)
              and name and name in (v["keys"].get("fable"), v["keys"].get("other"))]
        return sorted(ts, key=_task_key)

    keys = []
    for k in raw:
        if not isinstance(k, dict): continue
        name = str(k.get("name") or "")
        allow = k.get("allow") if isinstance(k.get("allow"), dict) else {}
        tok = str(k.get("token") or "")
        keys.append({"name": name, "allow": {"fable": allow.get("fable") is True, "other": allow.get("other") is True},
                     "enabled": k.get("enabled") is not False, "tail4": tok[-4:], "note": k.get("note") or "",
                     "issued": k.get("issued"), "last_used": ts_aware(k.get("last_used")) if k.get("last_used") else None,
                     "uses": k.get("uses") or 0, "in_use": in_use(name)})
    return {"keys": keys, "keys_file": str(path), "exists": exists, "error": error,
            # 系統ごとの候補数。0 の系統は PJ / 全体の env の鍵に落ちる（互換）
            "candidates": {g: sum(1 for k in keys if k["enabled"] and k["allow"][g]) for g in ("fable", "other")}}


def keys_apply(b):
    """鍵プールを変える（sandbox keys …）。action: add / set / rm / token。

    無効化（set --disable）と削除でその鍵を使えなくしたときは、使っている貸出中のチケットに reinject のジョブを起こす
    （動いている claude はそのまま。次の起動から別の鍵になる。#46 の規則）"""
    action, name = str(b.get("action") or ""), str(b.get("name") or "")
    if action not in ("add", "set", "rm", "token"): raise ApiError("action は add / set / rm / token のどれかにしてください")
    if not KEY_NAME_RE.match(name): raise ApiError("鍵の名前は英数字と . _ - の 1〜40 文字にしてください")
    token = b.get("token")
    stdin = None
    before = {k["name"]: k for k in keys_view()["keys"]}
    if action in ("add", "token"):
        if not token or not str(token).strip(): raise ApiError("トークンを入れてください")
        stdin = str(token).strip() + "\n"
    if action == "add":
        if not (b.get("fable") or b.get("other")): raise ApiError("「fable 許可」「fable 以外許可」のどちらか（両方でも可）を選んでください")
        args = ["keys", "add", name]
        if b.get("fable"): args.append("--fable")
        if b.get("other"): args.append("--other")
        if b.get("note"): args += ["--note", str(b["note"])]
    elif action == "token":
        args = ["keys", "token", name]
    elif action == "set":
        args = ["keys", "set", name]
        for g in ("fable", "other"):
            if b.get(g) is not None: args.append("--%s=%s" % (g, "on" if b[g] else "off"))
        if b.get("enabled") is not None: args.append("--enable" if b["enabled"] else "--disable")
        if b.get("note") is not None: args += ["--note", str(b["note"]) or "-"]
        if len(args) == 3: raise ApiError("変える項目がありません")
    else:
        args = ["keys", "rm", name] + (["--force"] if b.get("force") else [])
    rc, out, err = sandbox_cli(args, stdin=stdin)
    if rc != 0: raise ApiError((err or out).strip() or "sandbox keys %s が失敗しました（rc=%s）" % (action, rc))
    jobs = []
    if action == "rm" or (action == "set" and b.get("enabled") is False):
        for task in (before.get(name) or {}).get("in_use", []):
            try: jobs.append(JobStore.start("sandbox-reinject", ["sandbox", "reinject", task], "sandbox reinject %s" % task,
                                            ticket=int(task) if task.isdigit() else None))
            except Exception as e: jobs.append({"task": task, "error": "%s: %s" % (type(e).__name__, e)})
    return {"ok": True, "message": out.strip(), "reinject_jobs": jobs, "view": keys_view()}


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


# ---------- ログ（起票・配車）
# intake.log / dispatch.log の行は「console が読む契約」（ADR-0027）。glue 側の形式は変えず、ここで項目に分解する。
# どの規則にも当てはまらない行は event="other" にして原文（raw / reason）をそのまま出す（推測で埋めない。ADR-0025 と同じ姿勢）。
LOG_ENTRY = {"at": "", "source": "", "event": "other", "tid": None, "pj": "", "kind": "", "status": None,
             "rc": None, "elapsed_s": None, "confidence": None, "model": "", "reason": "", "detail": "",
             "dry_run": False, "raw": ""}
# dispatch.log の本文（日時の後ろ）。glue/bin/dispatch が書く 6 種類。上から順に当てる
DISPATCH_RULES = (
    ("end", re.compile(r"^end\s+(?P<tid>\d+) (?P<pj>\S+) (?P<kind>\S+) rc=(?P<rc>-?\d+) status=(?P<status>\S+) (?P<elapsed_s>\d+)s$")),
    ("start", re.compile(r"^start (?P<tid>\d+) (?P<pj>\S+) (?P<kind>\S+) ?(?P<title>.*)$")),
    ("blocked", re.compile(r"^(?P<tid>\d+) (?P<pj>\S+) (?P<kind>\S+): project\.yml 無し → blocked$")),
    ("worker_unavailable", re.compile(r"^(?P<tid>\d+) (?P<pj>\S+): Pull worker unavailable → skip$")),
    ("pool_busy", re.compile(r"^(?P<tid>\d+) (?P<pj>\S+): プール (?P<detail>\d+) 台すべて貸出中 → この PJ は飛ばす$")),
    ("idle", re.compile(r"^todo が無い")),
)
DRY_RUN_MARK = " (dry-run)"


def log_entry(**kw):
    e = dict(LOG_ENTRY); e.update(kw); return e


def as_int(v):
    try: return int(v)
    except (TypeError, ValueError): return None


def as_float(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def log_other(line, source):
    """規則に当てはまらない行。日時だけ切り出し、本文は原文のまま出す（隠さない・推測で埋めない）"""
    at, tab, body = line.partition("\t")
    if not tab: at, body = "", line
    return log_entry(at=at, source=source, event="other", reason=body, raw=line)


def parse_intake_line(line):
    """intake.log の 1 行（日時 / id / pj / kind / confidence / model / reason の 7 列、tab 区切り）"""
    if not line.strip(): return None
    c = line.split("\t", 6)
    if len(c) < 7: return log_other(line, "intake")
    at, tid, pj, kind, conf, model, reason = c
    return log_entry(at=at, source="intake", event="intake", tid=as_int(tid), pj=pj, kind=kind,
                     confidence=as_float(conf), model=model, reason=reason, raw=line)


def parse_dispatch_line(line):
    """dispatch.log の 1 行（日時 tab 本文）。本文は行の種類ごとに列が違うので規則を順に当てる"""
    if not line.strip(): return None
    at, tab, body = line.partition("\t")
    if not tab: return log_other(line, "dispatch")                  # 日時が無い行も原文として残す
    for name, rx in DISPATCH_RULES:
        m = rx.match(body)
        if not m: continue
        g = m.groupdict()
        e = log_entry(at=at, source="dispatch", raw=line, tid=as_int(g.get("tid")), pj=g.get("pj") or "", kind=g.get("kind") or "")
        if name == "end":
            e.update(event="end", rc=as_int(g["rc"]), status=g["status"], elapsed_s=as_int(g["elapsed_s"]))
        elif name == "start":
            title = g.get("title") or ""
            if title == DRY_RUN_MARK.strip(): title = ""; e["dry_run"] = True      # 題名が空の dry-run（印だけ残る）
            elif title.endswith(DRY_RUN_MARK): title = title[: -len(DRY_RUN_MARK)]; e["dry_run"] = True
            e.update(event="start", reason=title)
        elif name == "blocked":
            e.update(event="blocked", status="blocked")
        elif name == "idle":
            e.update(event="idle")
        else:
            e.update(event="skip", reason=name, detail=g.get("detail") or "")
        return e
    return log_other(line, "dispatch")


def logs_view():
    """生のログ（intake / dispatch）と、そこから導いた行の一覧（entries、新しい順）を返す。

    entries は画面の表と絞り込みが読む。生の text は「元のログを見る」と MCP の logs ツールが使うので消さない。
    """
    out = {}; rows = []
    for name in ("intake", "dispatch"):
        data, _ = read_file(str(LOGS / f"{name}.log"), tail=200_000); out[name] = data
        if not data: continue
        lines = data["text"].splitlines()
        if data.get("truncated") and lines: lines = lines[1:]        # tail の切れ目。先頭の不完全な 1 行は捨てる
        parse = parse_intake_line if name == "intake" else parse_dispatch_line
        rows += [e for e in (parse(l) for l in lines) if e]
    rows = [e for _, e in sorted(enumerate(rows), key=lambda t: (t[1]["at"], t[0]), reverse=True)]
    out["total"] = len(rows)
    out["entries"] = rows[:1000]                                     # 画面は新しい分だけ。件数は total で言う
    return out


# ---------- 工程ごとの消費統計（チケット 382）
# 根拠は run の生イベント agent-<step>-<n>.jsonl だけ: system/init の model、result の usage（input / cache_creation / cache_read / output）と
# total_cost_usd（claude CLI が API 料金で換算した値）、assistant の thinking ブロック。数字を推し量らず、無いものは 0 のまま。
# jsonl は 1 本 100 KB〜数 MB あるので、1 度読んだ結果は（サイズ・更新時刻を鍵に）メモリとディスクに置き、次からは読み直さない
STATS_CACHE = JOBS / "stats-cache.json"                        # ジョブ記録と同じ置き場（git 追跡外。checkout を汚さない）
_stats_mem = {}                                                  # path → {"key": [size, mtime], "row": {...}}
_stats_loaded = False
AGENT_JSONL = re.compile(r"^agent-(.+)-(\d+)\.jsonl$")


def _stats_load():
    global _stats_loaded
    if _stats_loaded: return
    _stats_loaded = True
    try:
        d = json.loads(STATS_CACHE.read_text(encoding="utf-8"))
        if isinstance(d, dict): _stats_mem.update({k: v for k, v in d.items() if isinstance(v, dict) and "key" in v and "row" in v})
    except (OSError, ValueError): pass


def _stats_save():
    try:
        STATS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATS_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_stats_mem, ensure_ascii=False), encoding="utf-8"); tmp.replace(STATS_CACHE)
    except OSError: pass


def step_stats(path):
    """agent-<step>-<n>.jsonl 1 本を読んで、その工程の消費を 1 行にする。result が無い（途中で切れた・古い形式）なら usage は 0 のまま"""
    row = {"model": None, "turns": 0, "duration_s": 0, "input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "cost": 0.0,
           "thinking_blocks": 0, "thinking_visible": 0, "thinking_chars": 0, "text_chars": 0, "tool_chars": 0, "tool_calls": 0, "tools": {}, "result": None, "rate_limited": False, "has_result": False}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try: ev = json.loads(line)
                except ValueError: continue
                t = ev.get("type")
                if t == "system" and ev.get("subtype") == "init": row["model"] = ev.get("model")
                elif t == "assistant":
                    for b in (ev.get("message") or {}).get("content") or []:
                        bt = b.get("type")
                        if bt == "thinking":
                            # Opus は thinking の本文が記録に出ない（空文字列と署名だけ）。回数は数え、本文が見える回だけ文字数を足す
                            row["thinking_blocks"] += 1
                            if b.get("thinking"): row["thinking_visible"] += 1; row["thinking_chars"] += len(b["thinking"])
                        elif bt == "text": row["text_chars"] += len(b.get("text") or "")
                        elif bt == "tool_use":
                            row["tool_calls"] += 1; n = str(b.get("name") or "?"); row["tools"][n] = row["tools"].get(n, 0) + 1
                            row["tool_chars"] += len(json.dumps(b.get("input") or {}, ensure_ascii=False))   # 見える出力（ツールの引数。編集の本文が大半）
                elif t == "rate_limit_event" and (ev.get("rate_limit_info") or {}).get("status") == "rejected": row["rate_limited"] = True
                elif t == "result":
                    u = ev.get("usage") or {}
                    row.update({"has_result": True, "result": ev.get("subtype"), "turns": int(ev.get("num_turns") or 0),
                                "duration_s": int(ev.get("duration_ms") or 0) // 1000,
                                "input": int(u.get("input_tokens") or 0), "cache_write": int(u.get("cache_creation_input_tokens") or 0),
                                "cache_read": int(u.get("cache_read_input_tokens") or 0), "output": int(u.get("output_tokens") or 0),
                                "cost": float(ev.get("total_cost_usd") or 0)})
    except OSError: pass
    return row


def agent_step_rows(force=False):
    """runs/ の全 agent-*.jsonl を 1 工程 1 行に。読んだ結果はキャッシュし、サイズか更新時刻が変わったものだけ読み直す（実行中の工程は毎回伸びる）"""
    _stats_load()
    rows, seen, dirty = [], set(), False
    if not RUNS.exists(): return rows
    for d in sorted(RUNS.iterdir()):
        if not d.is_dir() or d.name.startswith("."): continue
        m = RUN_NAME.match(d.name)
        if not m: continue
        st = None
        for f in d.iterdir():
            fm = AGENT_JSONL.match(f.name)
            if not fm: continue
            key = str(f); seen.add(key)
            try: stt = f.stat()
            except OSError: continue
            sig = [stt.st_size, int(stt.st_mtime)]
            c = _stats_mem.get(key)
            if force or not c or c["key"] != sig:
                c = {"key": sig, "row": step_stats(f)}; _stats_mem[key] = c; dirty = True
            if st is None:
                try: st = json.loads((d / "state.json").read_text(encoding="utf-8"))
                except (OSError, ValueError): st = {}
            row = dict(c["row"])
            row.update({"run": d.name, "date": d.name[:10], "pj": st.get("pj") or m.group(1), "task": st.get("task") or m.group(2),
                        "workflow": st.get("workflow"), "step": fm.group(1), "index": int(fm.group(2)),
                        "dry": d.name.endswith("-dry"), "attempt": bool(re.search(r"-attempt\d+$", d.name)),
                        "at": ts_file(f)})
            rows.append(row)
    for k in [k for k in _stats_mem if k not in seen]: del _stats_mem[k]; dirty = True
    if dirty: _stats_save()
    return rows


def _agg_into(a, r):
    a["steps"] += 1; a["turns"] += r["turns"]; a["duration_s"] += r["duration_s"]
    for k in ("input", "cache_write", "cache_read", "output", "cost", "thinking_blocks", "thinking_visible", "thinking_chars", "text_chars", "tool_chars", "tool_calls"): a[k] += r.get(k, 0)
    if r["thinking_blocks"]: a["steps_with_thinking"] += 1
    if r["rate_limited"]: a["rate_limited"] += 1
    if not r["has_result"]: a["no_result"] += 1
    if r["cost"] > a["max_cost"]: a["max_cost"] = r["cost"]; a["max_run"] = r["run"]; a["max_step_log"] = f"agent-{r['step']}-{r['index']}.log"


def _agg_new(**keys):
    return {**keys, "steps": 0, "turns": 0, "duration_s": 0, "input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "cost": 0.0,
            "thinking_blocks": 0, "thinking_visible": 0, "thinking_chars": 0, "text_chars": 0, "tool_chars": 0, "tool_calls": 0, "steps_with_thinking": 0, "rate_limited": 0, "no_result": 0,
            "max_cost": 0.0, "max_run": None, "max_step_log": None}


def stats_view(days=None, pj=None, include_dry=False):
    """工程ごとの消費統計。期間（今日からさかのぼる日数。None なら全部）と PJ で絞り、モデル別・工程別・日別・PJ 別・高い工程の上位にまとめる。
    費用は claude CLI の total_cost_usd（API 料金の換算値）の合計。利用枠（5 時間 / 7 日）の重みとは違うので、画面は「換算」と言う"""
    rows = agent_step_rows()
    since = (datetime.date.today() - datetime.timedelta(days=int(days) - 1)).isoformat() if days else None
    sel = [r for r in rows if (not since or r["date"] >= since) and (not pj or r["pj"] == pj) and (include_dry or not r["dry"])]
    by_model, by_step, by_day, by_pj = {}, {}, {}, {}
    total = _agg_new()
    for r in sel:
        model = r["model"] or "?"
        _agg_into(total, r)
        _agg_into(by_model.setdefault(model, _agg_new(model=model)), r)
        _agg_into(by_step.setdefault((r["step"], model), _agg_new(step=r["step"], model=model)), r)
        _agg_into(by_day.setdefault((r["date"], model), _agg_new(date=r["date"], model=model)), r)
        _agg_into(by_pj.setdefault(r["pj"], _agg_new(pj=r["pj"])), r)
    top = sorted(sel, key=lambda r: r["cost"], reverse=True)[:20]
    for r in top:
        r["log"] = f"agent-{r['step']}-{r['index']}.log"; r["log_path"] = rel(RUNS / r["run"] / r["log"]); r.pop("tools", None)
    return {"since": since, "days": days, "pj": pj, "pjs": sorted({r["pj"] for r in rows if r["pj"]}),
            "total": total,
            "by_model": sorted(by_model.values(), key=lambda a: -a["cost"]),
            "by_step": sorted(by_step.values(), key=lambda a: -a["cost"]),
            "by_day": sorted(by_day.values(), key=lambda a: (a["date"], -a["cost"]), reverse=True),
            "by_pj": sorted(by_pj.values(), key=lambda a: -a["cost"]),
            "top": top, "files": len(rows), "selected": len(sel), "cache_file": str(STATS_CACHE)}


def config_view():
    wfs = []
    for k in kinds():
        y = load_yaml(WORKFLOWS / f"{k}.yml")
        wfs.append({"name": k, "description": y.get("description", ""), "steps": [{"id": s.get("id"), "role": s.get("role"), "code": s.get("code")} for s in y.get("steps", [])], "start": y.get("start")})
    routes = dict(l.split("=", 1) for l in (KIT / "routes.env").read_text().splitlines() if l and not l.startswith("#") and "=" in l)
    rs = roles()
    git = subprocess.run(["git", "status", "--short", "--branch"], cwd=str(REPO), text=True, capture_output=True, errors="replace").stdout
    return {"workflows": wfs, "routes": routes, "roles": rs, "templates": sandbox_view()["templates"], "kb_root": str(KB_ROOT), "repo": str(REPO), "paths": paths.describe(), "git": git}
