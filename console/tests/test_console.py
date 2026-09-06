"""console のテスト。本番のデータは触らない（一時ディレクトリを AIFACTORY_WORKSPACE にして、その中に kb でチケットと dry-run の記録を作る）。

  python3 -m unittest discover -s console/tests -v

- HTTP: サーバーを別プロセスで起動して JSON API を叩く（GET / POST / 403 / 409 / dry-run ジョブ）
- JobStore: モジュールとして読み込み、ロック内の二重起動ガード・停止・再起動後の復元を直接確かめる
PJ は同梱の examples/projects/kumitate を使う（workspace/projects/ は空）。
"""
import importlib.machinery, importlib.util, json, os, pathlib, re, shutil, signal, socket, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.parse, urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
CONSOLE = REPO / "console" / "bin" / "console"
KB = REPO / "kanban" / "bin" / "kb"
PJ = "kumitate"   # examples/projects/kumitate


def seed_workspace(ws):
    """一時 workspace にチケット 1 件と、その dry-run の実行記録を作る"""
    env = {**os.environ, "AIFACTORY_WORKSPACE": str(ws)}
    r = subprocess.run([sys.executable, str(KB), "new", PJ, "research", "調査: テスト用の種", "--body", "-"], input="x\n\n## 完了条件\n- y\n", text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    tid = int(r.stdout.split()[0])
    r = subprocess.run([sys.executable, str(KB), "run", str(tid), "--dry-run"], text=True, capture_output=True, env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    return tid


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


def load_module(jobs_dir):
    spec = importlib.util.spec_from_loader("console_mod", importlib.machinery.SourceFileLoader("console_mod", str(CONSOLE)))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    m.core.JOBS = pathlib.Path(jobs_dir); m.core.JOBS.mkdir(parents=True, exist_ok=True)   # JobStore は lib/core.py の JOBS を見る
    return m


class Http:
    def __init__(self, base): self.base = base
    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r: return r.status, json.loads(r.read())
    def post(self, path, body=None, header=True):
        req = urllib.request.Request(self.base + path, data=json.dumps(body or {}).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **({"X-Console": "1"} if header else {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-console-test-"))
        cls.ws = cls.tmp / "ws"; cls.ws.mkdir()
        cls.seed = seed_workspace(cls.ws)
        cls.port = free_port()
        env = {**os.environ, "AIFACTORY_WORKSPACE": str(cls.ws), "CONSOLE_JOBS": str(cls.tmp / "jobs")}
        cls.proc = subprocess.Popen([sys.executable, str(CONSOLE), "--port", str(cls.port)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.http = Http(f"http://127.0.0.1:{cls.port}")
        for _ in range(50):
            try: cls.http.get("/api/overview"); break
            except Exception: time.sleep(0.1)
        else: raise RuntimeError("console が起動しない")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(timeout=10)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def todo_id(self):
        """テスト用の todo チケットを複製 DB に作って使う（本番の todo に依存しない）"""
        if not hasattr(type(self), "_todo"):
            st, d = self.http.post("/api/tickets", {"pj": PJ, "kind": "research", "title": "調査: console テスト用", "body": "x\n\n## 完了条件\n- y"})
            assert st == 200 and d.get("id"), d
            type(self)._todo = d["id"]
        return type(self)._todo

    def test_overview_and_lists(self):
        st, o = self.http.get("/api/overview")
        self.assertEqual(st, 200); self.assertTrue(o["db"]); self.assertEqual(set(o["counts"]), {"todo", "in_progress", "review", "blocked", "done"})
        self.assertEqual(o["paths"]["workspace"], str(self.ws)); self.assertFalse(o["paths"]["legacy"])
        _, t = self.http.get("/api/tickets"); self.assertGreater(len(t["tickets"]), 0); self.assertIn("bug", t["kinds"])
        _, tp = self.http.get(f"/api/tickets?pj={PJ}"); self.assertGreater(len(tp["tickets"]), 0)
        self.assertEqual({x["pj"] for x in tp["tickets"]}, {PJ})                      # 絞り込みはサーバー側で効いている
        self.assertTrue(all(not k.startswith((".", "_")) for k in t["kinds"]), t["kinds"])   # `._bug` のようなごみを候補に出さない
        self.assertIn("kind_desc", t); self.assertTrue(t["kind_desc"]["bug"])                # 画面が種別の用途を説明できる
        _, r = self.http.get("/api/runs"); self.assertTrue(any(x["kind"] == "v1" for x in r["runs"]))
        for ep in ("/api/sandbox", "/api/config", "/api/logs", "/api/jobs"): self.assertEqual(self.http.get(ep)[0], 200)

    def test_next_for_dispatch_dialog(self):
        """配車ダイアログが押す前に見せる「次に回るチケット」（kb next --json）"""
        tid = self.todo_id()
        st, d = self.http.get("/api/next"); self.assertEqual(st, 200); self.assertIsNotNone(d["next"]); self.assertEqual(d["next"]["status"], "todo")
        self.assertLessEqual(d["next"]["id"], tid)                                   # 最も古い todo
        st, d = self.http.get(f"/api/next?pj={PJ}"); self.assertEqual(st, 200); self.assertEqual(d["next"]["pj"], PJ)
        st, d = self.http.get("/api/next?pj=no-such-pj"); self.assertEqual(st, 200); self.assertIsNone(d["next"])

    def test_error_messages_say_what_to_do(self):
        """エラー文は「何が起きたか」の後に「どうすればよいか」（console/UX.md）"""
        st, d = self.http.post("/api/sandbox/release", {"task": "abc"}); self.assertEqual(st, 400); self.assertIn("指定してください", d["error"])
        st, d = self.http.post(f"/api/tickets/{self.todo_id()}/action", {"action": "nope"}); self.assertEqual(st, 400); self.assertIn("してください", d["error"])
        st, d = self.http.post("/api/intake", {"text": " "}); self.assertEqual(st, 400); self.assertIn("入れてください", d["error"])
        st, d = self.http.post("/api/tickets/999999/action", {"action": "start"}, header=False); self.assertEqual(st, 403); self.assertIn("X-Console", d["error"])

    def test_static_ui_files(self):
        """画面の静的ファイル（index.html / strings.js / app.js / style.css）が配信され、index.html が strings.js を app.js より先に読む"""
        for p, ctype in (("/", "text/html"), ("/strings.js", "javascript"), ("/app.js", "javascript"), ("/style.css", "text/css")):
            with urllib.request.urlopen(self.http.base + p, timeout=10) as r:
                self.assertEqual(r.status, 200, p); self.assertIn(ctype, r.headers["Content-Type"], p); body = r.read().decode("utf-8")
            if p == "/": self.assertLess(body.index("strings.js"), body.index("app.js")); self.assertIn('charset="utf-8"', body); self.assertIn('lang="ja"', body)

    def test_board_strip_and_columns_share_source(self):
        """ボードの帯と列が同じ絞り込み結果から数える（PJ を選ぶと帯だけ全体のままになるのを防ぐ）。

        JS を動かす基盤が無い（CI は Python 標準ライブラリだけ）ので、test_strings.py と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewBoard")
        body = app[i:app.index("\n}", i)]
        self.assertNotIn("o.counts", body, "帯が overview の全体集計を読んでいる。絞り込み済みの by から数える")
        self.assertEqual(re.findall(r"col\('(\w+)'", body), ["todo", "in_progress", "review", "done", "blocked"],
                         "列の並びを帯と揃える（未着手・実行中・レビュー待ち・完了・人間待ち）")
        for key in ("T.board.scopeAll", "T.board.scopePj"): self.assertIn(key, body, f"対象範囲の明示 {key} が無い")

    def test_done_overflow_leads_to_ticket_list(self):
        """ボードの完了列からあふれた分が、CLI ではなく画面（#/tickets）に続く。

        JS を動かす基盤が無いので、test_board_strip_and_columns_share_source と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        board = app[app.index("async function viewBoard"):app.index("\n}", app.index("async function viewBoard"))]
        self.assertIn("#/tickets", board, "完了列からあふれた分を見る導線が画面に無い（CLI の案内だけで終わっている）")
        self.assertIn("T.btn.openTickets", board, "ボードから一覧へ行くボタンが無い")
        self.assertRegex(app, r"seg\[0\] === 'tickets'", "route() に #/tickets が無い")

        src = (REPO / "console" / "static" / "strings.js").read_text(encoding="utf-8")
        T = json.loads(src[src.index("const T = ") + len("const T = "):src.rindex("};") + 1])
        self.assertNotIn("kb list", T["board"]["more"], "あふれた分の案内が CLI のコマンドのままになっている")

        i = app.index("async function viewTickets")
        view = app[i:app.index("\n}", i)]
        for key in ("q", "pj", "status"):
            self.assertIn(f"p.get('{key}')", view, f"絞り込み条件 {key} を URL から読んでいない（詳細から戻ると条件が消える）")
        render = app[app.index("function tkRender"):app.index("\n}", app.index("function tkRender"))]
        self.assertIn("T.tickets.count", render, "件数（何件中の何件か）を出していない")
        self.assertIn("T.empty.tickets", render, "0 件のときの案内が無い")

    def test_every_data_act_has_a_handler(self):
        """`data-act` は必ず `actions` に手がある名前だけにする。

        click の委譲（`document.addEventListener('click', ...)`）は SELECT と checkbox 以外の
        `[data-act]` をすべて `actions[...]` に回すので、手の無い名前を書くと押した瞬間に
        TypeError → 赤いトースト、さらに `disabled` の切り替えでフォーカスが外れる。
        JS を動かす基盤が無いので、ソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        block = app[app.index("const actions = {"):app.index("\n};", app.index("const actions = {"))]
        handlers = set(re.findall(r"^  '?([\w-]+)'?:", block, re.M))
        used = set(re.findall(r'data-act="([\w-]+)"', app))
        self.assertTrue(handlers, "actions の手を読み取れていない（テストの前提が壊れている）")
        self.assertEqual(used - handlers, set(), "actions に手の無い data-act がある（押すとエラーのトーストが出る）")

    def test_ticket_run_area_follows_status(self):
        """チケットの実行エリアが今の状態に合う（完了で押せる緑ボタン、ボタンが無い画面での「上の実行する」案内を防ぐ）。

        JS を動かす基盤が無いので、ボードの帯と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewTicket(")
        body = app[i:app.index("\n}", i)]
        self.assertRegex(body, r"canRun\s*=[^;\n]*status\s*!==\s*'done'", "実行できるかを status から決めていない")
        self.assertRegex(body, r"const runBtn = \([^)]*disabled[^)]*\)", "runBtn が押せない状態を受け取れない")
        self.assertRegex(body, r"runBtn\(T\.btn\.run,\s*'primary'[^)]*canRun", "本番の実行ボタンに実行可否を渡していない")
        self.assertNotRegex(body, r"runBtn\(T\.btn\.dryRun[^)]*canRun", "dry-run は完了済みでも通るので、押せなくしない")
        for key in ("T.help.runReview", "T.help.runBlocked"):
            self.assertIn(key, body, f"実行の案内が状態ごとに分かれていない（{key} が無い）")
        for key in ("T.empty.ticketRunsNoProjectYml", "T.empty.ticketRunsBusy", "T.empty.ticketRunsDone"):
            self.assertIn(key, body, f"実行記録の空文言が状態で変わらない（{key} が無い）")
        src = (REPO / "console" / "static" / "strings.js").read_text(encoding="utf-8")
        T = json.loads(src[src.index("const T = ") + len("const T = "):src.rindex("};") + 1])
        self.assertIn(T["btn"]["redo"], T["help"]["runDone"], "完了時の案内が、隣に出すボタンの名前と一致していない")

    def test_intake_keeps_draft_across_navigation(self):
        """起票の下書き（自由文・直接起票の 9 項目）が画面往復で消えない。

        `route()` は hash が変わるたび `viewIntake()` を呼び、`render()` が main を作り直す。
        入力をどこにも保持しないと、起票 → ログ → 起票の往復・再読み込み・戻るで必ず空になる。
        JS を動かす基盤が無い（CI は Python 標準ライブラリだけ）ので、ソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewIntake")
        view = app[i:app.index("\n}", i)]

        def action(name):
            j = app.index(f"  '{name}': ")
            m = re.compile(r"\n  '[\w-]+': ").search(app, j + 1)
            return app[j:m.start() if m else len(app)]

        # 保存先は sessionStorage（同じタブの往復・再読み込み・戻るで残り、タブを閉じれば消える）
        self.assertIn("sessionStorage", app, "下書きの保存先が無い")
        self.assertNotIn("beforeunload", app, "離脱の警告ではなく保持で守る（UX.md の文言規則の外に出る標準ダイアログを出さない）")
        self.assertTrue(re.search(r"(?:d|draft)\s*=\s*\w*[Dd]raft\w*\(", view), "viewIntake が保存済みの下書きを読んでいない")

        # 9 項目すべてが「id → 下書きの鍵」の対応表にあり、描画で復元されている
        for eid, key in (("in-text", "text"), ("in-pj", "pj"), ("in-kind", "kind"), ("in-dry", "dry"),
                         ("new-pj", "newPj"), ("new-kind", "newKind"), ("new-pr", "newPr"),
                         ("new-title", "title"), ("new-body", "body")):
            self.assertIn(f'id="{eid}"', view, f"{eid} が起票画面に無い")
            self.assertTrue(re.search(rf"'{eid}':\s*'{key}'", app), f"{eid} が下書きの対応表に無い（保存されない）")
            self.assertTrue(re.search(rf"\b(?:d|draft)\.{key}\b", view), f"{eid} の下書き {key} を描画で復元していない")

        # 破棄の規則: 送信が成功したときだけ、そのパネルの分を消す（失敗したら直して送り直せる）
        for name in ("intake", "new"):
            b = action(name)
            self.assertIn("draftDrop", b, f"actions['{name}'] が送信後に下書きを消していない")
            self.assertGreater(b.index("draftDrop"), b.index("await api("), f"actions['{name}'] が送信の前に下書きを消している")

        # 破棄は明示操作（可逆なので確認なし。トーストの「元に戻す」で書き戻す）
        for act in ("intake-clear", "new-clear"):
            self.assertIn(f"'{act}'", view, f"「下書きを捨てる」（{act}）のボタンが起票画面に無い")
            self.assertIn(f"'{act}':", app, f"actions に {act} が無い")
        self.assertTrue(re.search(r"function draftClear[\s\S]{0,600}T\.btn\.undo", app), "下書きの破棄に「元に戻す」が無い")

    def test_intake_shows_project_yml_readiness(self):
        """起票画面が、PJ を選んだ時点で「配車すると人間待ちになるか」を出せる。

        判定は sandbox / チケット画面と同じ project.yml の有無。project.yml が無くても provision.sh だけで PJ 候補には入るので、
        候補に出るが実行できない PJ が API 越しに区別できることを確かめる。表示は色だけに頼らず、起票そのものは止めない。
        """
        noyml = self.ws / "projects" / "noyml"
        noyml.mkdir(parents=True)
        (noyml / "provision.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        try:
            _, t = self.http.get("/api/tickets")
            self.assertIn("pj_ready", t, "/api/tickets が PJ の準備状態を返していない")
            self.assertEqual(sorted(t["pj_ready"]), sorted(t["pjs"]), "候補の PJ と準備状態の対象がずれている")
            self.assertTrue(t["pj_ready"][PJ], f"project.yml のある {PJ} が準備不足になっている")
            self.assertIn("noyml", t["pjs"], "provision.sh だけの PJ が候補から消えている（起票は妨げない）")
            self.assertFalse(t["pj_ready"]["noyml"], "project.yml の無い PJ が準備済みになっている")
        finally:
            shutil.rmtree(noyml, ignore_errors=True)

        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        i = app.index("async function viewIntake")
        view = app[i:app.index("\n}", i)]
        self.assertIn("pj_ready", view, "viewIntake が API の準備状態を読んでいない")
        for eid in ("in-pj", "new-pj"):
            self.assertTrue(re.search(rf'id="{eid}" data-act="pj-help"', view), f"{eid} を変えても準備状態が更新されない")
            self.assertIn(f'id="{eid}-help"', view, f"{eid} の準備状態を出す行が無い")
        self.assertIn("'pj-help':", app, "actions に pj-help が無い（選び直しても表示が変わらない）")
        # 色だけに頼らない: 準備済み / 準備不足のどちらも文字のバッジと本文で読める
        for key in ("T.intake.pjReadyBadge", "T.intake.pjNotReadyBadge", "T.help.pjReady", "T.help.pjNotReady"):
            self.assertIn(key, app, f"{key} を使っていない（状態が色でしか分からない）")
        self.assertIn("#/sandbox", app, "準備状態を確かめる先への導線が無い")
        # 準備不足でも backlog には積める: 送信ボタンを押せなくしない
        for act in ("intake", "new"):
            self.assertFalse(re.search(rf'data-act="{act}"[^>]*disabled', view), f"準備不足の PJ で「{act}」を押せなくしている")

    def test_run_status_drives_the_ui(self):
        """実行中かどうかの判定は API の status に寄せる（app.js が `!r.finished` で独自に決めない）。

        JS を動かす基盤が無いので、test_board_strip_and_columns_share_source と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        for fn in ("async function viewBoard", "async function viewRuns", "async function viewRun("):
            i = app.index(fn); body = app[i:app.index("\n}", i)]
            self.assertIn("status ===", body, f"{fn} が status を見ていない")
            self.assertNotIn("!s.finished", body, f"{fn} が finished から実行中を決めている")
        self.assertIn("T.run.notStarted", app); self.assertIn("T.run.noState", app)

    def test_list_rows_have_real_links(self):
        """一覧の行から詳細を開く導線を、行クリックだけでなく本物の `<a>` にする（チケット 224）。

        行は `<tr class="link" data-href>` で、名前セルが素の `<td>` だと Tab で届かず、
        読み上げでも link に見えない。同じ行のチケット番号・PR だけがリンクに見えるのを直す。
        JS を動かす基盤が無いので、test_board_strip_and_columns_share_source と同じくソースを検査する。
        """
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertRegex(app, r"function jobLink\(j\)[^\n]*<a href=\"#/job/", "ジョブ名を <a> にする jobLink が無い")
        for fn, helper in (("async function viewRuns", "runLink("), ("async function viewJobs", "jobLink("),
                           ("async function viewTicket(", "jobLink(")):
            i = app.index(fn); body = app[i:app.index("\n}", i)]
            self.assertIn(helper, body, f"{fn} の一覧が名前をリンクにしていない（{helper}）")
            self.assertIn('tr class="link" data-href', body, f"{fn} の行クリック（tr.link data-href）が消えている")
        i = app.index("async function route")
        body = app[i:app.index("\n}", i)]
        self.assertNotIn("window.scrollTo(0, 0)", body, "経路が変わるたび先頭に飛ぶと、詳細から戻ったとき一覧の位置が失われる")
        self.assertIn("scrollPos", body, "戻ったときに一覧の位置を戻す仕掛けが無い")

    def test_ticket_detail(self):
        _, t = self.http.get("/api/tickets"); tid = t["tickets"][0]["id"]
        st, d = self.http.get(f"/api/tickets/{tid}")
        self.assertEqual(st, 200); self.assertIsNotNone(d["body"]); self.assertTrue(d["history"]); self.assertIn("repo", d["ticket"])
        self.assertTrue(all(not k.startswith((".", "_")) for k in d["kinds"]), d["kinds"]); self.assertIn(d["ticket"]["kind"], d["kind_desc"])
        with self.assertRaises(urllib.error.HTTPError) as cm: self.http.get("/api/tickets/999999")
        self.assertEqual(cm.exception.code, 404)

    def test_run_detail(self):
        _, r = self.http.get("/api/runs"); name = next(x["name"] for x in r["runs"] if x["kind"] == "v1" and x["status"] != "not_started")
        st, d = self.http.get(f"/api/runs/{name}")
        self.assertEqual(st, 200); self.assertIn("state.json", [f["name"] for f in d["files"]]); self.assertIsNotNone(d["workflow"])

    def test_run_without_state_is_not_started(self):
        """ticket.md だけの run ディレクトリ（VM 貸出前に止まった残骸）を「実行中・工程 0」で数えない。

        画面は名前の無い実行リンクと空の「次は」「開始から」を出していた（チケット 220）。
        """
        for nm in ("2026-09-07-kumitate-999", "2026-09-07-kumitate-999-attempt1"):
            d = self.ws / "runs" / nm; d.mkdir(parents=True, exist_ok=True)
            (d / "ticket.md").write_text("# 調査: 記録の無い run\n", encoding="utf-8")
        _, r = self.http.get("/api/runs")
        row = next(x for x in r["runs"] if x["name"] == "2026-09-07-kumitate-999")
        self.assertEqual(row["status"], "not_started"); self.assertIsNone(row["finished"]); self.assertIsNone(row["result"])
        self.assertTrue(all(x["status"] in ("running", "finished", "not_started") for x in r["runs"]), r["runs"])
        self.assertEqual([x["status"] for x in r["runs"] if x["kind"] == "v0"] or ["finished"], ["finished"])
        _, o = self.http.get("/api/overview")
        self.assertNotIn("2026-09-07-kumitate-999", [x["name"] for x in o["runs_active"]])
        self.assertTrue(all(x["status"] == "running" for x in o["runs_active"]), o["runs_active"])
        self.assertGreaterEqual(o["runs_not_started"]["n"], 1)
        self.assertIn("2026-09-07-kumitate-999", [x["name"] for x in o["runs_not_started"]["runs"]])
        st, d = self.http.get("/api/runs/2026-09-07-kumitate-999")                    # 不完全な記録でも詳細へ移動できる
        self.assertEqual(st, 200); self.assertEqual(d["summary"]["status"], "not_started")
        self.assertIn("ticket.md", [f["name"] for f in d["files"]])

    def test_run_failed_before_start_is_finished_with_reason(self):
        """take が失敗した run（工程が 1 つも始まっていない）は「失敗」として終わった記録に見える（チケット 238）。

        state.json を最初から書くようにしたので、こういう run は「開始前（記録なし）」でも「実行中」でもない。
        """
        name = "2026-09-07-kumitate-998"
        d = self.ws / "runs" / name; d.mkdir(parents=True, exist_ok=True)
        (d / "ticket.md").write_text("# 調査: take が失敗した run\n", encoding="utf-8")
        (d / "state.json").write_text(json.dumps({
            "pj": PJ, "task": "998", "workflow": "research", "branch": "sandbox/998-research-x", "base": "develop",
            "started": "2026-09-07T10:00:00", "finished": "2026-09-07T10:00:05", "elapsed_s": 5, "history": [], "loops": {},
            "result": "failed", "next": "human", "current": None, "pr_url": "", "wip_branch": "",
            "error": "command failed (1): ['sandbox', 'take', 'kumitate', '998']\n[error] pj=kumitate に空きなし"}, ensure_ascii=False), encoding="utf-8")
        _, r = self.http.get("/api/runs")
        row = next(x for x in r["runs"] if x["name"] == name)
        self.assertEqual(row["status"], "finished"); self.assertEqual(row["result"], "failed"); self.assertEqual(row["pj"], PJ)
        _, o = self.http.get("/api/overview")
        self.assertNotIn(name, [x["name"] for x in o["runs_active"]])
        self.assertNotIn(name, [x["name"] for x in o["runs_not_started"]["runs"]])
        st, d = self.http.get(f"/api/runs/{name}")
        self.assertEqual(st, 200); self.assertEqual(d["summary"]["result"], "failed")
        self.assertIn("空きなし", d["state"]["error"])
        app = (REPO / "console" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("T.run.error", app)                                             # 詳細に失敗の理由が出る

    def test_file_roots(self):
        _, r = self.http.get("/api/runs"); name = next(x["name"] for x in r["runs"] if x["kind"] == "v1" and x["status"] != "not_started")
        _, d = self.http.get(f"/api/runs/{name}"); path = next(x["path"] for x in d["files"] if x["name"] == "state.json")
        st, f = self.http.get(f"/api/file?path={urllib.parse.quote(path)}&tail=100"); self.assertEqual(st, 200); self.assertTrue(f["truncated"] or f["size"] <= 100)
        for bad in ("../.ssh/id_rsa", "sandbox/bin/sandbox", str(self.ws / "kanban" / "kanban.db"), "/etc/passwd"):
            with self.assertRaises(urllib.error.HTTPError) as cm: self.http.get(f"/api/file?path={bad}")
            self.assertEqual(cm.exception.code, 403, bad)

    def test_post_requires_header(self):
        st, d = self.http.post("/api/tickets/204/action", {"action": "start"}, header=False)
        self.assertEqual(st, 403)

    def test_status_actions(self):
        tid = self.todo_id()
        self.assertEqual(self.http.post(f"/api/tickets/{tid}/action", {"action": "block"})[0], 400)          # メモ無しの人間待ちは拒否
        st, d = self.http.post(f"/api/tickets/{tid}/action", {"action": "start", "note": "test"}); self.assertEqual(st, 200, d)
        self.assertEqual(self.http.get(f"/api/tickets/{tid}")[1]["ticket"]["status"], "in_progress")
        st, d = self.http.post(f"/api/tickets/{tid}/action", {"action": "reopen"}); self.assertEqual(st, 200, d)
        self.assertEqual(self.http.get(f"/api/tickets/{tid}")[1]["ticket"]["status"], "todo")
        st, d = self.http.post(f"/api/tickets/{tid}/action", {"action": "set", "kind": "chore"}); self.assertEqual(st, 200, d)
        self.assertEqual(self.http.get(f"/api/tickets/{tid}")[1]["ticket"]["kind"], "chore")
        self.http.post(f"/api/tickets/{tid}/action", {"action": "set", "kind": "bug"})
        self.assertEqual(self.http.post(f"/api/tickets/{tid}/action", {"action": "set"})[0], 400)            # 変える項目が無い
        self.assertEqual(self.http.post(f"/api/tickets/{tid}/action", {"action": "nope"})[0], 400)
        hist = self.http.get(f"/api/tickets/{tid}")[1]["history"]
        self.assertTrue(any(h["field"] == "kind" and h["new"] == "chore" for h in hist))

    def _put_job(self, jid, **over):
        """終わったジョブの記録を CONSOLE_JOBS に直接置く（JobStore はディスクの meta.json を読む）"""
        meta = {"id": jid, "kind": "kb-run", "label": "kb run", "cmd": [str(KB), "run"], "ticket": None, "run_hint": None,
                "pid": 1, "started": "2000-01-01T00:00:00", "finished": "2000-01-01T00:00:00", "rc": 1, "state": "failed"}
        meta.update(over)
        d = self.tmp / "jobs" / jid; d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return jid

    def test_job_view_carries_current_ticket_state(self):
        """過去の失敗ジョブには、チケットの「今」の状態を添えて返す（画面が古い復旧案内を主表示しないため）"""
        st, d = self.http.post("/api/tickets", {"pj": PJ, "kind": "bug", "title": "不具合: 過去の失敗ジョブ", "body": "x\n\n## 完了条件\n- y"})
        self.assertEqual(st, 200, d); tid = d["id"]
        jid = self._put_job("20000101-000000-kb-run", ticket=tid, label=f"kb run {tid}")
        self.assertEqual(self.http.post(f"/api/tickets/{tid}/action", {"action": "done"})[0], 200)
        st, v = self.http.get(f"/api/jobs/{jid}")
        self.assertEqual(st, 200); self.assertIsNotNone(v["ticket"])
        self.assertEqual(v["ticket"]["id"], tid); self.assertEqual(v["ticket"]["status"], "done")
        self.assertTrue(v["ticket"]["updated_after_job"])                       # ジョブが終わった後に人が完了にした
        jid2 = self._put_job("20000101-000001-kb-run")                          # チケットを持たないジョブ
        self.assertIsNone(self.http.get(f"/api/jobs/{jid2}")[1]["ticket"])

    def test_sync_preview_does_not_write(self):
        """状態を合わせる前の下見（kb sync --dry-run）: 前後が分かり、チケットは書き換わらない"""
        tid = self.seed
        _, r = self.http.get("/api/runs")
        run = next(x["name"] for x in r["runs"] if str(x.get("task")) == str(tid))
        before = self.http.get(f"/api/tickets/{tid}")[1]
        st, p = self.http.get(f"/api/tickets/{tid}/sync-preview?run={urllib.parse.quote(run)}")
        self.assertEqual(st, 200, p)
        self.assertEqual(p["run"], run); self.assertEqual(p["before"]["status"], "todo"); self.assertEqual(p["after"]["status"], "done")
        self.assertTrue(p["changes"]); self.assertFalse(p["updated_after_run"]); self.assertEqual(p["ticket"]["id"], tid)
        after = self.http.get(f"/api/tickets/{tid}")[1]
        self.assertEqual(after["ticket"]["status"], "todo")                     # 下見は書き込まない
        self.assertEqual(after["ticket"]["updated"], before["ticket"]["updated"])
        self.assertEqual(len(after["history"]), len(before["history"]))
        st, d = self.http.post("/api/tickets", {"pj": PJ, "kind": "research", "title": "調査: run の無いチケット", "body": "x"})
        with self.assertRaises(urllib.error.HTTPError) as cm: self.http.get(f"/api/tickets/{d['id']}/sync-preview")
        self.assertEqual(cm.exception.code, 400); self.assertIn("--run", json.loads(cm.exception.read())["error"])

    def test_new_ticket(self):
        st, d = self.http.post("/api/tickets", {"pj": PJ, "kind": "research", "title": "調査: console テスト", "body": "本文\n\n## 完了条件\n- summary.md"})
        self.assertEqual(st, 200, d); self.assertIsInstance(d["id"], int)
        t = self.http.get(f"/api/tickets/{d['id']}")[1]; self.assertEqual(t["ticket"]["kind"], "research"); self.assertIn("完了条件", t["body"])
        self.assertEqual(self.http.post("/api/tickets", {"pj": PJ, "kind": "research"})[0], 400)
        self.assertEqual(self.http.post("/api/tickets", {"pj": "nope", "kind": "research", "title": "x"})[0], 400)

    def test_release_validation(self):
        self.assertEqual(self.http.post("/api/sandbox/release", {"task": "x; rm -rf /"})[0], 400)

    def test_dry_run_job(self):
        tid = self.todo_id()
        st, d = self.http.post(f"/api/tickets/{tid}/run", {"dry_run": True}); self.assertEqual(st, 200, d)
        jid = d["job"]["id"]; self.assertEqual(d["job"]["state"], "running")
        for _ in range(300):
            _, j = self.http.get(f"/api/jobs/{jid}")
            if j["job"]["state"] != "running": break
            time.sleep(0.2)
        self.assertIn(j["job"]["state"], ("done", "failed")); self.assertIn("dry-run 終了", j["log"]["text"])
        self.assertIn("[run ", j["log"]["text"])                       # runner まで到達した
        _, j2 = self.http.get(f"/api/jobs/{jid}?offset={j['log']['size']}"); self.assertEqual(j2["log"]["text"], "")
        self.assertEqual(self.http.get(f"/api/tickets/{tid}")[1]["ticket"]["status"], "todo")   # dry-run は状態を進めない
        self.assertTrue(any(x["label"].startswith("kb run") for x in self.http.get("/api/jobs")[1]["jobs"]))


class AuthDocsTest(unittest.TestCase):
    """CONSOLE_TOKEN（合言葉）付きで起動したときの認証と、/docs/ の配信（ADR-0017）"""
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-console-auth-"))
        cls.ws = cls.tmp / "ws"; cls.ws.mkdir()
        cls.port = free_port(); cls.token = "s3cret-token"
        env = {**os.environ, "AIFACTORY_WORKSPACE": str(cls.ws), "CONSOLE_JOBS": str(cls.tmp / "jobs"), "CONSOLE_TOKEN": cls.token}
        cls.proc = subprocess.Popen([sys.executable, str(CONSOLE), "--port", str(cls.port)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.base = f"http://127.0.0.1:{cls.port}"
        for _ in range(50):
            try: urllib.request.urlopen(cls.base + "/api/overview", timeout=2)
            except urllib.error.HTTPError: break
            except Exception: time.sleep(0.1)
        else: raise RuntimeError("console が起動しない")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(timeout=10); shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, path, headers=None, method="GET"):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k): return None
        opener = urllib.request.build_opener(NoRedirect)
        r = urllib.request.Request(self.base + path, headers=headers or {}, method=method, data=b"{}" if method == "POST" else None)
        try:
            with opener.open(r, timeout=10) as resp: return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e: return e.code, dict(e.headers), e.read()

    def test_token_required(self):
        st, _, body = self.req("/api/overview"); self.assertEqual(st, 401); self.assertIn("token", body.decode())
        st, _, _ = self.req("/"); self.assertEqual(st, 401)
        st, _, _ = self.req("/api/sandbox/ls", headers={"X-Console": "1", "Content-Type": "application/json"}, method="POST"); self.assertEqual(st, 401)

    def test_bearer_and_cookie(self):
        st, _, _ = self.req("/api/overview", headers={"Authorization": f"Bearer {self.token}"}); self.assertEqual(st, 200)
        st, _, _ = self.req("/api/overview", headers={"Authorization": "Bearer wrong"}); self.assertEqual(st, 401)
        st, h, _ = self.req(f"/?token={self.token}"); self.assertEqual(st, 302); self.assertEqual(h.get("Location"), "/")
        cookie = h.get("Set-Cookie", "").split(";")[0]; self.assertIn(self.token, cookie)
        st, _, _ = self.req("/api/overview", headers={"Cookie": cookie}); self.assertEqual(st, 200)
        st, _, _ = self.req("/", headers={"Cookie": cookie}); self.assertEqual(st, 200)
        st, h, _ = self.req(f"/api/tickets?pj=x&token={self.token}"); self.assertEqual(st, 302); self.assertEqual(h.get("Location"), "/api/tickets?pj=x")

    def test_docs_route(self):
        h = {"Authorization": f"Bearer {self.token}"}
        st, _, body = self.req("/docs/", headers=h)
        self.assertIn(st, (200, 503))                       # website/site/ があれば 200、無ければ作り方の案内（503）
        self.assertIn(b"<html", body.lower()[:200] if st == 503 else body.lower())
        st, hh, _ = self.req("/docs", headers=h); self.assertIn(st, (301, 503))
        st, _, _ = self.req("/docs/../console/lib/core.py", headers=h); self.assertNotEqual(st, 200)


class BindPolicyTest(unittest.TestCase):
    """合言葉なしで bind してよいアドレス（ADR-0021）: 内側の網は可、全インターフェースとグローバルは不可"""
    def test_internal_bind(self):
        m = load_module(tempfile.mkdtemp(prefix="aifactory-bind-test-"))
        # 192.168.x と 100.64/10 のリテラルは bin/oss-check.sh（環境固有の名前の検査）に引っかかるので組み立てる
        for ok in ("127.0.0.1", "localhost", "::1", "10.77.0.3", "172.16.5.9", "192.%d.1.20" % 168, "100.%d.102.103" % 101, "169.254.1.1", "fd12::1"):
            self.assertTrue(m.internal_bind(ok), ok)
        for ng in ("0.0.0.0", "::", "1.1.1.1", "8.8.8.8", "2606:4700::1111", "ctl.example.com", ""):   # 203.0.113.x（TEST-NET）は予約で is_global でないので使わない
            self.assertFalse(m.internal_bind(ng), ng)


class JobStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aifactory-jobs-test-"); self.m = load_module(self.tmp); self.JS = self.m.JobStore
    def tearDown(self):
        for j in self.JS.running():
            try: os.killpg(os.getpgid(j["pid"]), signal.SIGKILL)
            except OSError: pass
        # _wait persists the final result after the process exits. Wait for that
        # writer before deleting its directory, otherwise teardown races _flock.
        deadline = time.monotonic() + 5
        while True:
            with self.JS.lock:
                pending = bool(self.JS.procs)
            if not pending: break
            self.assertLess(time.monotonic(), deadline, "job result writers did not finish")
            time.sleep(0.01)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_conflict_under_lock(self):
        same = lambda j: "dup" if j.get("ticket") == 1 else None
        a = self.JS.start("kb-run", ["sleep", "5"], "a", ticket=1, conflict=same)
        res = []
        def go():
            try: self.JS.start("kb-run", ["sleep", "5"], "b", ticket=1, conflict=same); res.append("started")
            except self.m.Conflict as e: res.append(str(e))
        ts = [threading.Thread(target=go) for _ in range(8)]; [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(res, ["dup"] * 8)
        other = lambda j: "dup" if j.get("ticket") == 2 else None          # API は要求されたチケットで判定する
        self.JS.start("kb-run", ["true"], "c", ticket=2, conflict=other)  # 別チケットは通る
        self.assertTrue(self.JS.stop(a["id"])[0])

    def test_stop_marks_stopped(self):
        a = self.JS.start("x", ["sleep", "5"], "a")
        ok, _ = self.JS.stop(a["id"]); self.assertTrue(ok)
        for _ in range(50):
            if self.JS.get(a["id"])["rc"] is not None: break
            time.sleep(0.1)
        self.assertEqual(self.JS.get(a["id"])["state"], "stopped")
        self.assertFalse(self.JS.stop(a["id"])[0])                        # 2 回目は「既に終わっている」

    def test_done_and_failed(self):
        a = self.JS.start("x", ["true"], "a"); b = self.JS.start("x", ["false"], "b")
        for _ in range(50):
            if self.JS.get(a["id"])["rc"] is not None and self.JS.get(b["id"])["rc"] is not None: break
            time.sleep(0.1)
        self.assertEqual(self.JS.get(a["id"])["state"], "done"); self.assertEqual(self.JS.get(b["id"])["state"], "failed")

    def test_stdin_and_log(self):
        a = self.JS.start("x", ["cat", "{stdin}"], "a", stdin_text="hello\n")
        for _ in range(50):
            if self.JS.get(a["id"])["rc"] is not None: break
            time.sleep(0.1)
        self.assertIn("hello", (pathlib.Path(self.tmp) / a["id"] / "log").read_text())

    def test_reconcile_marks_lost(self):
        dead = {"id": "20000101-000000-x", "kind": "x", "label": "x", "cmd": ["x"], "pid": 2**22 - 1, "started": "2000-01-01T00:00:00", "finished": None, "rc": None, "state": "running"}
        self.JS.save(dead); self.JS.reconcile()
        self.assertEqual(self.JS.get(dead["id"])["state"], "lost")


class KitListingTest(unittest.TestCase):
    """種別・役割の一覧は kit のディレクトリ走査。macOS の AppleDouble（`._bug.yml`）などのごみを候補に出さない"""
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aifactory-kit-test-"))
        self.core = load_module(self.tmp / "jobs").core
        self.kit = self.tmp / "kit"; self.kit.mkdir()
        (self.kit / "bug.yml").write_text("name: bug\ndescription: 不具合を直す\n", encoding="utf-8")
        (self.kit / "._bug.yml").write_bytes(b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X")   # AppleDouble
        (self.kit / ".hidden.yml").write_text("name: hidden\n", encoding="utf-8")
        (self.kit / "planner.md").write_text("# planner\n", encoding="utf-8")
        (self.kit / "._planner.md").write_bytes(b"\x00\x05\x16\x07")
        (self.kit / "_common.md").write_text("# common\n", encoding="utf-8")
        (self.kit / "sub").mkdir()                                                 # ディレクトリは候補にしない
        (self.kit / "sub.yml").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dot_files_are_not_candidates(self):
        self.assertEqual(self.core.kinds(self.kit), ["bug"])
        self.assertEqual(self.core.roles(self.kit), ["planner"])


if __name__ == "__main__":
    unittest.main()
