"""票の参照を 2 列に分ける: 外部 issue（取得可否つき）と内部の票番号（チケット 556 / ADR-0084）。

  python3 -m unittest discover -s workflow/tests -p 'test_ticket_issue_refs.py' -v

VM も claude も使わない（一時 workspace と examples/projects/kumitate だけで自立している）。
守りたいのは「取りに行けるように見えて絶対に取れない参照」を run に渡さないこと:

- 外部 issue の URL は `related_issue`、aifactory の内部票番号は `related_ticket`。互いに混ぜて書けない
- 取得可否は 2 語（`readable` / `unreadable`）で、書かなければ `unreadable` に倒す（sandbox のトークンは issues に 403 を返す）
- 列が無かった頃の `kanban.db` には kb が足す。本文に URL が自由文で埋まっている既存票は今までどおり動く
- researcher の依頼文には「issues は読めない」が必ず入る（正本は役割文書 1 枚）
"""
import importlib.machinery
import importlib.util
import contextlib
import io
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
KB = REPO / "kanban" / "bin" / "kb"
ROLE = REPO / "workflow" / "kit" / "roles" / "researcher.md"
PJ = "kumitate"   # examples/projects/kumitate

spec = importlib.util.spec_from_loader("issue_refs_run", importlib.machinery.SourceFileLoader("issue_refs_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

ISSUE = "https://github.com/akkijp/kumitate/issues/393"
# 実際に踏んだ形（#521）。本文に URL が自由文で埋まっていて、指しているのは内部の票番号
BODY = f"""# 内部番号の参照を持つ票

## 背景
{ISSUE} の続き。

## 完了条件
- 直っている
"""


class KbHarness(unittest.TestCase):
    """kb を一時 workspace の別プロセスで叩く（DB も tickets も走らせた機械に残さない）"""

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.env = {**os.environ, "AIFACTORY_WORKSPACE": str(self.ws)}

    def kb(self, *args, stdin=None):
        return subprocess.run([sys.executable, str(KB), *map(str, args)], input=stdin, text=True,
                              capture_output=True, env=self.env)

    def new(self, *args, tid=700, body="x\n", title="参照の分離"):
        return self.kb("new", PJ, "chore", title, "--id", tid, "--body", "-", *args, stdin=body)

    def meta(self, tid, field):
        """`kb show` の 1 行から列の値を読む（空なら ''）"""
        r = self.kb("show", tid)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = next(l for l in r.stdout.splitlines() if l.startswith(field + " "))
        return line[len(field):].strip()


class ReferenceColumnsTest(KbHarness):

    def test_an_external_url_and_an_internal_number_live_in_different_columns(self):
        """run が「取りに行ける参照か」を構造から判断できる（本文の自由文を読み解かせない）"""
        r = self.new("--issue", ISSUE, "--ticket", "521, 521 556")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.meta(700, "related_issue"), ISSUE)
        self.assertEqual(self.meta(700, "related_ticket"), "521,556")      # 重複は落ち、並び順は書いたまま

    def test_an_issue_without_a_flag_is_taken_as_unreadable(self):
        """既定は安全側。sandbox のトークンは issues に 403 を返すので、確かめていない参照は取りに行かせない"""
        self.assertEqual(self.new("--issue", ISSUE).returncode, 0)
        self.assertEqual(self.meta(700, "related_issue_access"), "unreadable")

    def test_readable_is_written_only_when_someone_says_so(self):
        self.assertEqual(self.new("--issue", ISSUE, "--issue-access", "readable").returncode, 0)
        self.assertEqual(self.meta(700, "related_issue_access"), "readable")
        r = self.kb("set", 700, "--issue-access", "unreadable")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.meta(700, "related_issue_access"), "unreadable")

    def test_the_two_kinds_of_reference_cannot_be_written_into_each_other(self):
        """この票の事故そのもの: 内部番号を外部 issue の欄に書けてしまうと、また取りに行かれる"""
        self.assertEqual(self.new().returncode, 0)
        for bad in ("393", "#393", "akkijp/kumitate#393"):
            r = self.kb("set", 700, "--issue", bad)
            self.assertEqual(r.returncode, 1, (bad, r.stdout))
            self.assertIn("--ticket", r.stderr)                             # 書く先を案内する
        for bad in (ISSUE, "39x", "#393"):
            self.assertEqual(self.kb("set", 700, "--ticket", bad).returncode, 1, bad)
        self.assertEqual(self.kb("set", 700, "--ticket", "700").returncode, 1)   # 自分自身は書き間違い
        self.assertEqual(self.meta(700, "related_issue"), "")
        self.assertEqual(self.meta(700, "related_ticket"), "")

    def test_an_unknown_access_word_is_refused(self):
        self.assertEqual(self.new("--issue", ISSUE, "--issue-access", "maybe").returncode, 2)   # argparse

    def test_clearing_the_issue_clears_its_flag_with_it(self):
        """URL が無いのに取得可否だけ残ると、次に読む側がまた推し量ることになる"""
        self.assertEqual(self.new("--issue", ISSUE, "--issue-access", "readable", "--ticket", "521").returncode, 0)
        r = self.kb("set", 700, "--issue", "")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.meta(700, "related_issue"), "")
        self.assertEqual(self.meta(700, "related_issue_access"), "")
        self.assertEqual(self.meta(700, "related_ticket"), "521")           # 内部番号は消さない（別の列）
        self.assertEqual(self.kb("set", 700, "--ticket", "").returncode, 0)
        self.assertEqual(self.meta(700, "related_ticket"), "")

    def test_the_flag_needs_an_issue_to_describe(self):
        self.assertEqual(self.new().returncode, 0)
        r = self.kb("set", 700, "--issue-access", "readable")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("--issue", r.stderr)

    def test_the_changes_are_left_in_the_history(self):
        self.assertEqual(self.new("--issue", ISSUE, "--ticket", "521").returncode, 0)
        self.assertEqual(self.kb("set", 700, "--issue-access", "readable").returncode, 0)
        h = self.kb("history", 700).stdout
        self.assertIn("related_issue ", h)
        self.assertIn("related_ticket", h)
        self.assertIn("unreadable → readable", h)


class ExistingTicketTest(KbHarness):
    """移行はしない。本文に URL が埋まっている既存票は今までどおり（列は空のまま）"""

    def test_a_ticket_with_the_url_in_its_body_still_works(self):
        r = self.new(body=BODY, tid=701)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.meta(701, "related_issue"), "")
        self.assertEqual(self.meta(701, "related_ticket"), "")
        self.assertIn(ISSUE, self.kb("show", 701).stdout)                   # 本文はそのまま読める
        self.assertEqual(json.loads(self.kb("next", "--pj", PJ, "--json").stdout)["id"], 701)
        self.assertEqual(self.kb("set", 701, "--note", "x").returncode, 0)  # 列を持たない票の更新も変わらない


class OldDatabaseTest(KbHarness):
    """運用中の kanban.db には列が無い。CREATE TABLE IF NOT EXISTS は既存表を変えないので kb が足す（#570 と同じ形）"""

    def test_kb_adds_the_new_columns_to_a_database_that_predates_them(self):
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
        r = self.new("--issue", ISSUE, "--ticket", "521", tid=802)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.meta(802, "related_issue"), ISSUE)
        self.assertEqual(self.meta(801, "related_issue"), "")               # 既存の行は空のまま（挙動が変わらない）
        self.assertEqual(json.loads(self.kb("next", "--pj", PJ, "--json").stdout)["id"], 801)


class ResearcherBriefTest(unittest.TestCase):
    """★実測で確かめられるのはここまで（実 run での「取得試行 0 回」は ctl 側で見る）。
       依頼文は役割文書をそのまま貼るので、researcher が受け取る文面に必ず入ることを合成した票で示す"""

    def fake_run(self, ticket):
        """build_prompt() だけを動かす最小の Run（VM も workflow の読み込みも要らない）"""
        r = run.Run.__new__(run.Run)
        r.project = {"name": PJ, "repo": "akkijp/kumitate", "app_dir": "/home/dev/app", "stack": "Python"}
        r.ticket, r.title, r.wf_name = ticket, "内部番号の参照を持つ票", "chore"
        r.branch, r.base, r.work, r.dry = "sandbox/556-chore", "develop", "~/work/556", True
        r.state = {"attachments": []}
        return r

    def brief(self, ticket, role="researcher"):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.fake_run(ticket).build_prompt({"id": "research", "role": role, "outputs": ["research.md"]})

    def test_the_researcher_brief_says_issues_cannot_be_read(self):
        b = self.brief(BODY)
        self.assertIn("issues", b)
        self.assertIn("Resource not accessible by integration", b)
        self.assertIn("取りに行かず", b)
        self.assertIn(ISSUE, b)              # 票の本文はそのまま渡る（読み替え表を人が手で足さなくて済む）

    def test_the_line_lives_in_the_role_file_so_every_researcher_step_carries_it(self):
        """依頼文を組み立てる側に文面を書き写さない（役割文書 1 枚が正本）"""
        lines = [l for l in ROLE.read_text(encoding="utf-8").splitlines() if "Resource not accessible by integration" in l]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("related_issue", lines[0])       # 例外（readable）と内部番号の行き先まで 1 行に収める
        self.assertIn("related_ticket", lines[0])
        self.assertNotIn("Resource not accessible by integration", (REPO / "workflow/bin/run").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
