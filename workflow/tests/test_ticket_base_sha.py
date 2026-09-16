"""票に「起票時の base sha」を機械が刻み、run が古さを依頼文の 1 行で知らせる（チケット 554 / ADR-0091）。

  python3 -m unittest discover -s workflow/tests -p 'test_ticket_base_sha.py' -v

VM も claude も使わない（一時 workspace と examples/projects/kumitate だけで自立している）。
守りたいのは「何十コミットも前の行番号を、現在のものとして run に渡さない」こと:

- `kb new` が PJ の base_branch の tip（git の commit sha。config 編集の `base_sha256` とは無関係）を自動で刻む
- 取れない環境（gh も sandbox も無い CI / この VM）でも起票は落ちない。stderr に理由が 1 行出るだけ
- `kb run` が値を runner へ渡し、距離を測るのは base を fetch した後の VM の中（制御系は PJ の clone を持たない）
- 依頼文に出るのは「## チケット」直下の 1 行だけ。距離 0 と sha 無しの票では 1 行も足さない（依頼文を膨らませない）
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
KB = REPO / "kanban" / "bin" / "kb"
ROLE = REPO / "workflow" / "kit" / "roles" / "researcher.md"
PJ = "kumitate"   # examples/projects/kumitate（repo: akkijp/kumitate / base_branch: develop）

spec = importlib.util.spec_from_loader("base_sha_run", importlib.machinery.SourceFileLoader("base_sha_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


def load_kb():
    """kb を「スクリプトとしてではなく」読み込む（fetch_base_sha を関数として直接呼ぶため）"""
    spec = importlib.util.spec_from_loader("base_sha_kb", importlib.machinery.SourceFileLoader("base_sha_kb", str(KB)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

SHA = "df252fbf0" + "a" * 31          # #526 が刻めていたら入っていたはずの値（40 桁）
TOKEN = "ghs_fake_token_value"
BODY = "# 引用を持つ票\n\n`live-app-pane.tsx:120` を直す。\n"

# gh api repos/<repo>/commits/<branch> --jq .sha の代わり。応答は環境変数 SHA_OUT で差し替える
FAKE_GH = r"""#!/usr/bin/env bash
echo "gh $*" >> "$CALLS"
if [ -n "${GH_SLEEP-}" ]; then sleep "$GH_SLEEP"; fi
if [ -n "$GH_FAILS" ]; then echo "gh: Not Found (HTTP 404)" >&2; exit 1; fi
echo "${SHA_OUT-}"
"""

FAKE_SANDBOX = r"""#!/usr/bin/env bash
echo "$@" >> "$CALLS"
case "$1" in
  gh-app) [ "$2" = token ] && echo "TOKEN_VALUE" ;;
  take) echo "[error] pj=$2 に空きなし" >&2; exit 1 ;;
esac
exit 0
""".replace("TOKEN_VALUE", TOKEN)


class KbHarness(unittest.TestCase):
    """kb を一時 workspace の別プロセスで叩く（DB も tickets も走らせた機械に残さない）"""

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.bin = self.ws / "bin"; self.bin.mkdir()
        self.empty = self.ws / "empty-bin"; self.empty.mkdir()   # gh も sandbox も無い PATH（この VM / CI と同じ）
        for name, body in (("gh", FAKE_GH), ("sandbox", FAKE_SANDBOX)):
            f = self.bin / name; f.write_text(body); f.chmod(0o755)
        self.calls = self.ws / "calls.log"
        self.env = {**os.environ, "AIFACTORY_WORKSPACE": str(self.ws), "PATH": f"{self.bin}:{os.environ['PATH']}",
                    "CALLS": str(self.calls), "SHA_OUT": SHA, "GH_FAILS": ""}
        for k in ("GH_TOKEN", "AIFACTORY_FROM_RUN", "AIFACTORY_RESUME_RUN"):
            self.env.pop(k, None)

    def kb(self, *args, env=None, stdin=None):
        return subprocess.run([sys.executable, str(KB), *map(str, args)], input=stdin, text=True,
                              capture_output=True, env=env or self.env)

    def new(self, *args, tid=700, body=BODY, env=None):
        return self.kb("new", PJ, "chore", "起票時の base sha", "--id", tid, "--body", "-", *args, stdin=body, env=env)

    def meta(self, tid, field):
        """`kb show` の 1 行から列の値を読む（空なら ''）"""
        r = self.kb("show", tid)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = next(l for l in r.stdout.splitlines() if l.startswith(field + " "))
        return line[len(field):].strip()

    def calls_text(self):
        return self.calls.read_text(encoding="utf-8") if self.calls.exists() else ""


class StampingTest(KbHarness):
    """完了条件「`ticket_new` / `intake` の両方で base sha が自動記録される」——
       intake も console も `kb new` を subprocess で呼ぶだけなので、刻む場所は kb の 1 か所（ADR-0015）"""

    def test_kb_new_stamps_the_tip_of_the_project_base_branch(self):
        r = self.new()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.meta(700, "base_sha"), SHA)
        # 人が書くのではなく機械が刻む。聞きに行った先は PJ 定義の repo と base_branch
        self.assertIn("gh api repos/akkijp/kumitate/commits/develop", self.calls_text())
        self.assertIn("gh-app token kumitate", self.calls_text())

    def test_the_other_mouth_files_through_kb_so_it_gets_the_stamp_too(self):
        """完了条件の「intake でも自動記録される」の根拠。intake も console も `kb new` を subprocess で呼ぶだけなので、
        刻む実装は kb の 1 か所で足りる（ADR-0015）。ここが直接 INSERT に変わったらこの検査で気づく"""
        for path, needle in ((REPO / "glue/bin/intake", '"new"'), (REPO / "console/lib/core.py", '"new"')):
            src = path.read_text(encoding="utf-8")
            self.assertIn(needle, src, path)
            self.assertNotIn("INSERT INTO tickets", src, f"{path} が kb を通さずに票を作っている")

    def test_the_stamp_is_left_in_the_history(self):
        self.assertEqual(self.new().returncode, 0)
        self.assertIn("base_sha", self.kb("history", 700).stdout)

    def test_the_token_value_never_reaches_the_ticket(self):
        self.assertEqual(self.new().returncode, 0)
        r = self.kb("show", 700)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)
        self.assertNotIn(TOKEN, self.kb("history", 700).stdout)

    def test_an_explicit_sha_is_taken_without_asking_github(self):
        """テストと、人が後から訂正するときの口。明示したら gh を呼ばない"""
        r = self.new("--base-sha", "DEADbee")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.meta(700, "base_sha"), "deadbee")     # 大文字は小文字に揃える
        self.assertEqual(self.calls_text(), "")

    def test_a_value_that_is_not_a_sha_is_refused(self):
        for bad in ("zzzzzzz", "abc", "0" * 41, "https://github.com/x/y"):
            r = self.new("--base-sha", bad)
            self.assertEqual(r.returncode, 1, (bad, r.stdout))
            self.assertIn("--base-sha", r.stderr)

    def test_an_empty_value_means_do_not_stamp(self):
        r = self.new("--base-sha", "")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.meta(700, "base_sha"), "")
        self.assertEqual(self.calls_text(), "")                     # 「刻まない」と言われたら聞きに行かない

    def test_a_wrong_stamp_can_be_corrected_and_cleared_by_hand(self):
        self.assertEqual(self.new().returncode, 0)
        self.assertEqual(self.kb("set", 700, "--base-sha", "1234abc").returncode, 0)
        self.assertEqual(self.meta(700, "base_sha"), "1234abc")
        self.assertEqual(self.kb("set", 700, "--base-sha", "").returncode, 0)
        self.assertEqual(self.meta(700, "base_sha"), "")
        self.assertIn("base_sha", self.kb("history", 700).stdout)   # 直した跡は残る
        self.assertEqual(self.kb("set", 700, "--base-sha", "zz").returncode, 1)


class StampingNeverBreaksNewTest(KbHarness):
    """★起票は絶対に落とさない。sha が取れないのは「刻まない」だけで、票は今までどおり作られる"""

    def no_tools(self):
        return dict(self.env, PATH=str(self.empty))

    def test_a_machine_without_gh_or_sandbox_still_files_the_ticket(self):
        r = self.new(env=self.no_tools())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.meta(700, "base_sha"), "")
        self.assertEqual(len([l for l in r.stderr.splitlines() if l.startswith("[kb] warn:")]), 1, r.stderr)
        self.assertNotIn("[kb] warn", r.stdout)                     # 警告は stderr だけ（stdout は票の 1 行が正）

    def test_the_warning_is_not_the_capability_warning_console_collects(self):
        """`[kb] warning:` は console の ticket_new が warnings[] に拾う印（552）。そこへ混ぜない"""
        r = self.new(env=self.no_tools())
        self.assertNotIn("[kb] warning:", r.stderr)

    def test_a_failing_github_is_just_a_missing_stamp(self):
        r = self.new(env=dict(self.env, GH_FAILS="1"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.meta(700, "base_sha"), "")
        self.assertIn("[kb] warn:", r.stderr)

    def test_an_answer_that_is_not_a_sha_is_not_stamped(self):
        r = self.new(env=dict(self.env, SHA_OUT="Not Found"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.meta(700, "base_sha"), "")
        self.assertIn("[kb] warn:", r.stderr)


class OldDatabaseTest(KbHarness):
    """運用中の kanban.db には列が無い。CREATE TABLE IF NOT EXISTS は既存表を変えないので kb が足す（#570 と同じ形）"""

    def test_kb_adds_the_column_to_a_database_that_predates_it(self):
        (self.ws / "kanban" / "tickets").mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.ws / "kanban" / "kanban.db")
        con.executescript("""
            CREATE TABLE tickets (id INTEGER PRIMARY KEY, pj TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'todo', file TEXT NOT NULL, pr INTEGER, run TEXT, note TEXT,
              created TEXT NOT NULL, updated TEXT NOT NULL, depends_on TEXT);
            CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
              at TEXT NOT NULL, field TEXT NOT NULL, old TEXT, new TEXT);
            INSERT INTO tickets VALUES (801, 'kumitate', 'chore', '列が無かった頃の票', 'todo',
              'tickets/801-x.md', NULL, NULL, NULL, '2026-01-01T00:00:00', '2026-01-01T00:00:00', NULL);
        """)
        con.commit(); con.close()
        r = self.new(tid=802)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.meta(802, "base_sha"), SHA)
        self.assertEqual(self.meta(801, "base_sha"), "")            # 既存の行は空のまま（挙動が変わらない）
        self.assertEqual(json.loads(self.kb("next", "--pj", PJ, "--json").stdout)["id"], 801)
        self.assertEqual(self.kb("set", 801, "--note", "x").returncode, 0)


class StampingDoesNotWidenTheIdRaceTest(KbHarness):
    """GitHub に聞くのは id を採番する前（554 のレビュー指摘 1）。

    `kb new` は `SELECT MAX(id)` で id を決めてから INSERT する。その間にネットワーク（実運用で 1〜3 秒）を挟むと
    同時に起票した 2 本が同じ id を採る窓がその分だけ広がり、片方が UNIQUE 違反で落ちて .md だけが孤児で残る。
    採番の窓は刻印を入れる前と同じ（ms）ままであること"""

    def new_async(self, title):
        """id を指定しない起票（採番を kb に任せる＝窓が開く形）を 1 本起こす。

        本文は stdin ではなくファイルで渡す——stdin で渡すと書き込みの順で 5 本が直列になり、窓が開かない"""
        body = self.ws / "body.md"
        if not body.exists(): body.write_text(BODY, encoding="utf-8")
        return subprocess.Popen([sys.executable, str(KB), "new", PJ, "chore", title, "--body", str(body)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env={**self.env, "GH_SLEEP": "1"})

    def test_tickets_filed_at_the_same_time_all_survive(self):
        procs = [self.new_async(f"同時起票 {i}") for i in range(5)]
        outs = [p.communicate() for p in procs]
        for p, (out, err) in zip(procs, outs):
            self.assertEqual(p.returncode, 0, out + err)
            self.assertNotIn("Traceback", err)
        con = sqlite3.connect(self.ws / "kanban" / "kanban.db")
        ids = [r[0] for r in con.execute("SELECT id FROM tickets")]
        shas = con.execute("SELECT COUNT(*) FROM tickets WHERE base_sha = ?", (SHA,)).fetchone()[0]
        con.close()
        files = list((self.ws / "kanban" / "tickets").glob("*.md"))
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 5, "同じ id を 2 本が採った")
        self.assertEqual(shas, 5)                                   # 前に出しても刻印は落ちない
        self.assertEqual(len(files), len(ids), "台帳に無い .md が残った（採番の窓で起票が落ちている）")


class GithubCannotStallTheFilingTest(KbHarness):
    """固まった `sandbox` / `gh` で起票が止まらない（554 のレビュー指摘 2）。

    `kb new` は今までネットワークに触らない経路で、呼ぶ側（console の ticket_new / MCP / intake）は
    子プロセスに上限を渡していない。上限は gh api の脚だけでなくトークンの払い出しにも要る"""

    def test_a_hanging_token_command_becomes_a_reason_not_a_wait(self):
        sys.path.insert(0, str(REPO / "lib")); self.addCleanup(sys.path.remove, str(REPO / "lib"))
        import aifactory_gh
        hang = self.bin / "sandbox"; hang.write_text("#!/usr/bin/env bash\nsleep 30\n"); hang.chmod(0o755)
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}"}; env.pop("GH_TOKEN", None)
        old = dict(os.environ); os.environ.clear(); os.environ.update(env)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(old)))
        t0 = time.monotonic()
        why = aifactory_gh.ensure_gh_token(PJ, timeout=1)
        self.assertLess(time.monotonic() - t0, 15, "上限が掛かっていない（払い出しが返るまで待っている）")
        self.assertIn("1s", why)
        self.assertNotIn("GH_TOKEN", os.environ)

    def test_kb_puts_a_limit_on_both_legs_of_the_lookup(self):
        """トークンの払い出しと gh api の両方に秒数が渡ること（片方だけでは固まる）"""
        kb = load_kb()
        seen = {}

        def ensure(pj, repo="", log=None, timeout=None):
            seen["token"] = timeout
            os.environ["GH_TOKEN"] = "x"
            return None

        def branch_sha(repo, branch, timeout=None):
            seen["api"] = timeout
            return SHA, None

        self.addCleanup(setattr, kb.gh, "ensure_gh_token", kb.gh.ensure_gh_token)
        self.addCleanup(setattr, kb.gh, "branch_sha", kb.gh.branch_sha)
        self.addCleanup(os.environ.pop, "GH_TOKEN", None)
        kb.gh.ensure_gh_token, kb.gh.branch_sha = ensure, branch_sha
        kb._GH_TOKENS.clear(); kb.GH_AMBIENT = ""
        sha, why = kb.fetch_base_sha(PJ)
        self.assertEqual((sha, why), (SHA, None))
        self.assertTrue(seen.get("token"), "トークンの払い出しに上限が掛かっていない")
        self.assertTrue(seen.get("api"), "gh api に上限が掛かっていない")


class BriefHarness(unittest.TestCase):
    """build_prompt() だけを組み立てて読む土台（VM も claude も要らない）"""

    def fake_run(self, refs=None, state=None):
        r = run.Run.__new__(run.Run)
        r.project = {"name": PJ, "repo": "akkijp/kumitate", "app_dir": "/home/dev/app", "stack": "Python"}
        r.ticket, r.title, r.wf_name = BODY, "引用を持つ票", "chore"
        r.branch, r.base, r.work, r.dry = "sandbox/554-chore", "develop", "~/work/554", True
        r.state = {"attachments": [], **(state or {})}
        r.refs = {k: v for k, v in (refs or {}).items() if v}
        return r

    def brief(self, refs=None, state=None, role="researcher"):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.fake_run(refs, state).build_prompt({"id": "research", "role": role, "outputs": ["research.md"]})


class StalenessInTheBriefTest(BriefHarness):
    """完了条件「`ticket_run` の依頼文に古さの警告が入る（差が 0 なら入らない）」"""

    def test_the_distance_is_stated_in_commits_right_above_the_body(self):
        b = self.brief(refs={"base_sha": SHA}, state={"base_sha": SHA, "base_distance": 51})
        self.assertIn(f"## チケット\n\n- 注意: この票の行番号は 51 commits 前（{SHA[:9]}）のもの。着手時に必ず自分で数え直すこと。\n\n# 引用を持つ票", b)

    def test_a_base_that_has_not_moved_adds_nothing(self):
        """読み替えが要らない run に注意書きを増やさない（依頼文は既に長い）"""
        same = self.brief(refs={"base_sha": SHA}, state={"base_sha": SHA, "base_distance": 0})
        self.assertEqual(same, self.brief())
        self.assertNotIn("- 注意: この票の行番号", same)

    def test_a_value_that_is_not_a_sha_never_reaches_the_brief(self):
        """通常は kb が形を検査してから渡すが、手で起こした run でも読めない値を依頼文に書かない（554 のレビュー指摘 3）"""
        self.assertIsNone(run.base_sha_or_none("not-a-sha-xxx"))
        self.assertIsNone(run.base_sha_or_none(""))
        self.assertIsNone(run.base_sha_or_none(None))
        self.assertEqual(run.base_sha_or_none(" " + SHA.upper() + " "), SHA)
        bad = self.brief(refs={"base_sha": "not-a-sha-xxx"}, state={"base_distance": 51})
        self.assertEqual(bad, self.brief())
        self.assertNotIn("not-a-sha", bad)

    def test_a_ticket_without_a_stamp_reads_exactly_as_before(self):
        """完了条件「既存票（sha を持たない）でも起動が壊れない」——欠損時は警告を出さないだけ"""
        self.assertEqual(self.brief(refs={}), self.brief(refs=None))
        self.assertNotIn("- 注意: この票の行番号", self.brief())

    def test_a_distance_that_cannot_be_counted_says_so_instead_of_pretending_it_is_zero(self):
        """0 に丸めると「ずれていない」と読めてしまい、この票が塞ごうとした事故そのものになる"""
        b = self.brief(refs={"base_sha": SHA}, state={"base_sha": SHA, "base_distance": None})
        self.assertIn(f"- 注意: この票の行番号は起票時の base（{SHA[:9]}）のもの。現在との距離は測れなかった。着手時に必ず自分で数え直すこと。", b)

    def test_the_notice_is_one_line_and_comes_before_the_quoted_line_numbers(self):
        b = self.brief(refs={"base_sha": SHA}, state={"base_sha": SHA, "base_distance": 51})
        notice = [l for l in b.splitlines() if l.startswith("- 注意: この票の行番号")]
        self.assertEqual(len(notice), 1, notice)
        self.assertLess(b.index(notice[0]), b.index("live-app-pane.tsx:120"))

    def test_the_researcher_is_told_to_measure_the_quotes_again(self):
        """仕様 3（差が大きいときの再測定）の最小形。文面の正本は役割文書 1 枚（ADR-0015）"""
        lines = [l for l in ROLE.read_text(encoding="utf-8").splitlines() if "commits 前" in l]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("測り直", lines[0])
        self.assertIn(lines[0].strip()[3:20], self.brief(role="researcher"))    # 依頼文に役割文書がそのまま貼られる


class DistanceOnTheVmTest(unittest.TestCase):
    """距離を測るのは base を fetch した後の VM の中（制御系は PJ の clone を持たない）。
       `self.sb` を本物の bash に差し替えて、実際の git リポジトリで数える"""

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.repo = pathlib.Path(d.name) / "app"
        self.repo.mkdir(parents=True)
        self.git("init", "-q", "-b", "develop")
        self.git("config", "user.email", "t@example.com"); self.git("config", "user.name", "t")
        self.shas = []
        for i in range(4):
            (self.repo / "f.txt").write_text(f"{i}\n")
            self.git("add", "f.txt"); self.git("commit", "-qm", f"c{i}")
            self.shas.append(self.git("rev-parse", "HEAD").stdout.strip())
        self.git("update-ref", "refs/remotes/origin/develop", "HEAD")      # fetch 済みの VM と同じ形

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], text=True, capture_output=True, check=True)

    def fake_run(self, sha):
        r = run.Run.__new__(run.Run)
        r.dry, r.base, r.refs, r.state = False, "develop", {"base_sha": sha}, {}
        r.save = lambda: None
        r.sb = lambda cmd, input_text=None, check=True: subprocess.run(
            ["bash", "-c", cmd], text=True, capture_output=True, check=False,
            env={**os.environ, "SANDBOX_APP_DIR": str(self.repo)}).stdout
        return r

    def test_it_counts_the_commits_between_the_stamp_and_the_current_base(self):
        r = self.fake_run(self.shas[0])
        r.measure_base_distance()
        self.assertEqual(r.state["base_distance"], 3)
        self.assertEqual(r.state["base_sha"], self.shas[0])

    def test_the_tip_itself_is_zero_commits_away(self):
        r = self.fake_run(self.shas[-1])
        r.measure_base_distance()
        self.assertEqual(r.state["base_distance"], 0)

    def test_a_sha_the_vm_cannot_reach_is_unknown_not_zero(self):
        """浅い fetch や force-push で辿れないことがある。例外にもせず、0 にも丸めない"""
        r = self.fake_run("0" * 40)
        r.measure_base_distance()
        self.assertIsNone(r.state["base_distance"])
        self.assertEqual(r.state["base_sha"], "0" * 40)

    def test_a_ticket_without_a_stamp_is_not_measured_at_all(self):
        r = self.fake_run("")
        r.measure_base_distance()
        self.assertEqual(r.state, {})


class DryRunBriefTest(KbHarness):
    """kb run が値を runner へ渡すところまでを、dry-run（VM も claude も使わない）で見る"""

    def prompt(self, tid):
        r = self.kb("run", tid, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        ds = list((self.ws / "runs").glob(f"*-{PJ}-{tid}-dry"))
        self.assertEqual(len(ds), 1, ds)
        ps = sorted(ds[0].glob("prompt-*.md"))
        self.assertTrue(ps, list(ds[0].iterdir()))
        return r.stdout, ps[0].read_text(encoding="utf-8")

    def test_kb_run_hands_the_stamp_to_the_runner(self):
        self.assertEqual(self.new(tid=990).returncode, 0)
        out, brief = self.prompt(990)
        self.assertIn(f"--base-sha={SHA}", out)
        # dry-run は VM を借りないので距離は測れない。0 に丸めず「測れなかった」と言う
        self.assertIn(f"- 注意: この票の行番号は起票時の base（{SHA[:9]}）のもの。現在との距離は測れなかった。", brief)

    def test_a_ticket_without_a_stamp_gets_no_flag_and_no_line(self):
        self.assertEqual(self.new("--base-sha", "", tid=991).returncode, 0)
        out, brief = self.prompt(991)
        self.assertNotIn("--base-sha", out)
        self.assertNotIn("- 注意: この票の行番号", brief)
        self.assertIn("\n\n# 引用を持つ票", brief)                  # 本文の前に余計な空行も増えていない


if __name__ == "__main__":
    unittest.main()
