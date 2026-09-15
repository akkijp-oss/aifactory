"""実行環境の能力宣言（project.yml の `capabilities`）と票の完了条件の照合（チケット 552）。

  python3 -m unittest discover -s workflow/tests -p 'test_capabilities.py' -v

VM も claude も使わない（偽 claude と一時 workspace、examples/projects/kumitate だけで自立している）。
守りたいのは「実行できないことを可視化して人へ渡す」であって、条件を緩めることではない。だから:

- 照合するのは `## 完了条件` の節だけ（節が無ければ本文全体。その旨を scope に残す）
- 宣言が false の能力だけを見る。**未宣言は照合しない**（false＝無いと確かめた、とは違う）
- 当たっても **起票は止まらない**（rc 0・票は作られる）。警告は stderr に出るだけ
- 依頼文の節は false が 1 つ以上あるときだけ足す
- `report.md` の `## 未検証項目` は空欄で出せない（「無し」と書けば通る）
"""
import io
import contextlib
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
KB = REPO / "kanban" / "bin" / "kb"
INTAKE = REPO / "glue" / "bin" / "intake"

sys.path.insert(0, str(REPO / "lib"))
import aifactory_capabilities as caps   # noqa: E402

spec = importlib.util.spec_from_loader("capabilities_run", importlib.machinery.SourceFileLoader("capabilities_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

OFF = {"browser": False, "docker": False, "gui": False}

BODY = """# 機能: iframe を出す

## 背景
ブラウザで見たときに崩れる、という報告がある（ここは完了条件ではない）。

## 完了条件
- ローカルの合成データとブラウザで実 iframe を検証する
- `docker compose` で起動して通ること
- `pnpm test` が緑
"""

# #552 の票の抜粋。実行不能な手段を**例として**挙げているが、完了条件はどれもこの環境で実行できる
TICKET_552 = """# sandbox 環境の能力を project.yml で宣言する

## あるべき仕様
2. `intake` / `ticket_new` が完了条件を走査し、宣言と矛盾する要求（「ブラウザで目視」「docker compose で起動」
   「外部から取得」）を起票時に警告する。

### (a) 完了条件が未達のまま着地した（kumitate #526）
票の完了条件に「ローカルの認証付き合成データとブラウザで実 iframe を検証」があった。run 環境にブラウザが無い。

## 完了条件
- backend ごとの能力宣言が定義に存在し、`project_show` で読める。
- 実行不能な完了条件を含む票を `ticket_new` したとき警告が出る（ブロックはしない）。
- `report.md` に未検証項目欄があり、空欄のまま提出できない。

## 範囲外
- 能力そのものを増やすこと（sandbox にブラウザや docker を入れるのは別の判断）。
"""

FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, sys
body = "背景です。\n\n## 完了条件\n- ブラウザで目視して確かめる\n- テストが緑\n"
print("```json\n" + json.dumps({"pj": "kumitate", "kind": "bug", "title": "fix: 直す", "body": body,
                                "confidence": 0.9, "reason": "テスト"}, ensure_ascii=False) + "\n```")
"""


class ScanTest(unittest.TestCase):
    """辞書と走査の規則（lib 単体。ここが判定の正本）"""

    def test_only_the_done_section_is_scanned(self):
        """例示で「ブラウザ」に触れた背景の行は当たらない。完了条件の中の 2 行だけが当たる"""
        r = caps.scan(BODY, OFF)
        self.assertEqual(r["scope"], "完了条件")
        self.assertEqual([h["cap"] for h in r["hits"]], ["browser", "docker"])
        self.assertIn("実 iframe", r["hits"][0]["text"])
        self.assertNotIn("報告がある", " ".join(h["text"] for h in r["hits"]))

    def test_a_body_without_a_done_section_is_scanned_whole_and_says_so(self):
        r = caps.scan("## 背景\nブラウザで見て直す\n", OFF)
        self.assertEqual(r["scope"], "body")
        self.assertEqual([h["cap"] for h in r["hits"]], ["browser"])
        self.assertIn("`## 完了条件` の節が無い", "\n".join(caps.format_warnings(r)))

    def test_undeclared_capabilities_are_not_checked(self):
        """未宣言は「照合していない」。false（無いと確かめた）と混ぜない（ADR-0077 決定 4 と同じ扱い）"""
        self.assertEqual(caps.scan(BODY, {})["hits"], [])
        self.assertEqual(caps.scan(BODY, {"docker": False})["off"], ["docker"])
        self.assertEqual([h["cap"] for h in caps.scan(BODY, {"docker": False})["hits"]], ["docker"])

    def test_capabilities_declared_true_never_warn(self):
        self.assertEqual(caps.scan(BODY, {"browser": True, "docker": True})["hits"], [])

    def test_ascii_terms_match_on_word_boundaries(self):
        """`compose` が `composer` に当たらない（誤検知を増やさないため）"""
        self.assertEqual(caps.scan("## 完了条件\n- composer を使う\n", OFF)["hits"], [])
        self.assertEqual([h["cap"] for h in caps.scan("## 完了条件\n- Docker で起動\n", OFF)["hits"]], ["docker"])

    def test_localhost_words_are_not_in_the_dictionary(self):
        """`curl` / `e2e` は localhost 用途と衝突するので初期語彙に入れない（推測で増やさない）"""
        flat = [t for terms in caps.VOCAB.values() for t in terms]
        self.assertNotIn("curl", flat)
        self.assertNotIn("e2e", flat)

    def test_gui_is_derived_from_computer_use_only_when_not_declared(self):
        self.assertEqual(caps.declared({"computer_use": True}), {"gui": True})
        self.assertEqual(caps.declared({"computer_use": True, "capabilities": {"gui": True}}), {"gui": True})
        self.assertEqual(caps.declared({"computer_use": False}), {})          # 他の 3 つは導出しない
        self.assertEqual(caps.declared({"capabilities": {"docker": False}}), {"docker": False})
        self.assertEqual(caps.declared({"capabilities": {"docker": "no"}}), {})   # bool 以外は宣言と見なさない
        self.assertEqual(caps.declared(None), {})

    def test_examples_declare_what_this_repository_measured(self):
        """同梱の PJ 定義が宣言を持っている（完了条件 1 番目の読み口はここ）"""
        self.assertEqual(caps.load_declared("aifactory"),
                         {"browser": False, "docker": False, "egress": True, "gui": False})
        # kumitate の egress は未実測なので書いていない（＝照合しない）
        self.assertNotIn("egress", caps.load_declared("kumitate"))
        self.assertEqual(caps.load_declared("そんな-pj-は-無い"), {})

    def test_a_ticket_that_only_talks_about_browsers_does_not_warn(self):
        """★誤検知の見本。#552 の票自身は「ブラウザで目視」「docker compose」を**例として**挙げるが、
        完了条件はどれも実行可能である。全体を走査すると 8 行当たるところが、節に絞ると 0 行になる
        （2026-09-15 に #552 の本文で実測。節を絞る設計の根拠）"""
        r = caps.scan(TICKET_552, caps.load_declared("aifactory"))
        self.assertEqual(r["scope"], "完了条件")
        self.assertEqual(r["hits"], [], [h["text"] for h in r["hits"]])
        # 節が無い票（全体を走査する）なら、同じ本文がすべて誤検知として当たる
        whole = caps.scan(TICKET_552.replace("## 完了条件", "## 受け入れ"), caps.load_declared("aifactory"))
        self.assertEqual(whole["scope"], "body")
        self.assertGreater(len(whole["hits"]), 0)

    def test_warnings_say_the_match_may_be_wrong_and_never_ask_to_delete_the_condition(self):
        w = "\n".join(caps.format_warnings(caps.scan(BODY, OFF)))
        self.assertIn("外れもある", w)
        self.assertIn("条件を消さずに", w)


class PromptSectionTest(unittest.TestCase):
    def test_no_section_when_nothing_is_declared_false(self):
        self.assertEqual(caps.prompt_section({}, caps.scan(BODY, {})), "")
        self.assertEqual(caps.prompt_section({"docker": True}, caps.scan(BODY, {"docker": True})), "")

    def test_section_lists_the_missing_capabilities_and_the_matching_lines(self):
        s = caps.prompt_section(OFF, caps.scan(BODY, OFF))
        self.assertIn("## この環境で検証できないこと", s)
        for cap in ("browser", "docker", "gui"): self.assertIn(f"**{cap}**", s)
        self.assertIn("実 iframe", s)
        self.assertIn("ゲートが全部緑でも", s)
        self.assertIn("完了条件を削らない", s)      # ★ 条件を緩める方向へ倒さない
        self.assertIn("## 未検証項目", s)

    def test_build_prompt_puts_the_section_before_the_ticket(self):
        p = build_prompt(fake_run({"capabilities": dict(OFF)}, BODY))
        self.assertTrue(has_section(p))
        self.assertLess(p.index("\n## この環境で検証できないこと\n"), p.index("\n## チケット\n"))
        self.assertIn("実 iframe", p.split("## チケット")[0])       # 当たった完了条件が節にも載る

    def test_build_prompt_is_unchanged_for_projects_without_a_declaration(self):
        """宣言の無い PJ の依頼文は今までどおり（節ごと出ない）"""
        self.assertFalse(has_section(build_prompt(fake_run({}, BODY))))
        self.assertFalse(has_section(build_prompt(fake_run({"capabilities": {"docker": True}}, BODY))))

    def test_every_role_gets_the_section(self):
        """reviewer も読む（未検証項目と照合するため）"""
        for role in ("implementer", "reviewer", "researcher", "planner"):
            self.assertTrue(has_section(build_prompt(fake_run({"capabilities": dict(OFF)}, BODY), role)),
                            f"{role} の依頼文に節が無い")

    def test_a_project_yml_that_cannot_be_read_does_not_break_the_prompt(self):
        r = fake_run({}, BODY); r.project = {"name": "x", "repo": "a/b", "app_dir": "/a", "capabilities": "壊れている"}
        self.assertFalse(has_section(build_prompt(r)))


def has_section(prompt):
    """依頼文に節が「見出しとして」在るか。役割の説明文がこの語を含むので、行頭の見出しだけを見る"""
    return any(l.strip() == "## この環境で検証できないこと" for l in prompt.splitlines())


def fake_run(project, ticket):
    """build_prompt() だけを動かす最小の Run（VM も workflow の読み込みも要らない）"""
    r = run.Run.__new__(run.Run)
    r.project = {"name": "kumitate", "repo": "akkijp/kumitate", "app_dir": "/home/dev/app", **project}
    r.ticket, r.title, r.wf_name = ticket, "iframe を出す", "feature"
    r.branch, r.base, r.work, r.dry = "sandbox/100-feature", "develop", "~/work/100", True
    r.state = {"attachments": []}
    return r


def build_prompt(r, role="implementer"):
    with contextlib.redirect_stdout(io.StringIO()):
        return r.build_prompt({"id": "implement", "role": role, "outputs": ["report.md", "git"]})


class ReportUnverifiedTest(unittest.TestCase):
    """report.md の `## 未検証項目`。実装役の良心ではなく runner が見る"""

    def test_a_report_without_the_section_is_not_accepted(self):
        ok, why = run.report_unverified("# 実装報告: x\n## 変更したファイルと理由\n- a\n")
        self.assertFalse(ok)
        self.assertIn("## 未検証項目", why)

    def test_an_empty_section_is_not_accepted(self):
        ok, why = run.report_unverified("# 実装報告: x\n## 未検証項目\n\n## 残した懸念\n- b\n")
        self.assertFalse(ok)
        self.assertIn("空", why)

    def test_writing_none_passes(self):
        """書くことを増やす仕掛けではない。無いなら「無し」と書けば通る"""
        self.assertEqual(run.report_unverified("# 実装報告: x\n## 未検証項目\n無し\n## 残した懸念\n- b\n"), (True, ""))

    def test_the_listed_conditions_pass(self):
        ok, _ = run.report_unverified("# 実装報告: x\n\n## 未検証項目\n- ブラウザで実 iframe を検証: この VM に画面が無い\n")
        self.assertTrue(ok)

    def test_empty_report(self):
        self.assertFalse(run.report_unverified("")[0])
        self.assertFalse(run.report_unverified(None)[0])

    def test_the_role_template_carries_a_heading_the_check_accepts(self):
        """テンプレートの見出し行が、そのまま runner の検査に合う形であること（見出しの字面がずれると必ず戻される）"""
        t = (REPO / "workflow/kit/roles/implementer.md").read_text(encoding="utf-8")
        heads = [l for l in t.splitlines() if run.UNVERIFIED_HEADING.match(l.strip())]
        self.assertEqual(len(heads), 1, f"implementer.md の未検証項目の見出し: {heads}")
        # テンプレートは見出しだけ（中身は実装役が書く）。1 行でも書けば通る
        self.assertTrue(run.report_unverified(heads[0] + "\n無し\n")[0])


class KbNewTest(unittest.TestCase):
    """起票は止めない。警告は stderr に出るだけ"""

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.env = {**os.environ, "AIFACTORY_WORKSPACE": str(self.ws)}

    def kb(self, *args, input_text=None):
        return subprocess.run([sys.executable, str(KB), *map(str, args)], input=input_text, text=True,
                              capture_output=True, env=self.env)

    def new(self, body=BODY, tid=100, pj="kumitate"):
        return self.kb("new", pj, "feature", "iframe", "--id", tid, "--body", "-", input_text=body)

    def test_the_ticket_is_created_even_when_the_conditions_cannot_be_met(self):
        r = self.new()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[kb] warning:", r.stderr)
        self.assertIn("browser", r.stderr)
        self.assertIn("docker", r.stderr)
        self.assertTrue(list((self.ws / "kanban" / "tickets").glob("100-*.md")), "票が作られていない")
        self.assertNotIn("warning", r.stdout)     # 標準出力は今までどおり（id を拾う経路を壊さない）

    def test_no_warning_when_nothing_matches(self):
        r = self.new(body="## 完了条件\n- `pnpm test` が緑\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("[kb] warning:", r.stderr)

    def test_a_broken_project_yml_does_not_break_ticket_creation(self):
        """kb が yaml を読むようになった。読めなくても起票は落ちない"""
        d = self.ws / "projects" / "kumitate"; d.mkdir(parents=True)
        (d / "project.yml").write_text("name: kumitate\n  : [壊れている\n", encoding="utf-8")
        r = self.new()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(list((self.ws / "kanban" / "tickets").glob("100-*.md")))

    def test_capcheck_counts_hits_over_the_real_tickets(self):
        self.new(tid=100)
        self.new(tid=101, body="## 完了条件\n- `pnpm test` が緑\n")
        r = self.kb("capcheck", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hits 1 / scanned 2", r.stdout)
        j = json.loads(self.kb("capcheck", "--all", "--json").stdout)
        self.assertEqual((j["scanned"], j["hits"], j["skipped"]), (2, 1, 0))
        self.assertEqual(j["tickets"][0]["id"], 100)
        self.assertEqual([h["cap"] for h in j["tickets"][0]["hits"]], ["browser", "docker"])

    def test_capcheck_does_not_count_projects_without_a_declaration(self):
        """「照合して 0 件」と「照合していない」を混ぜない"""
        d = self.ws / "projects" / "kumitate"; d.mkdir(parents=True)
        (d / "project.yml").write_text("name: kumitate\nrepo: x/y\napp_dir: /a\n", encoding="utf-8")
        self.new(tid=100)
        r = self.kb("capcheck", "--all")
        self.assertIn("hits 0 / scanned 0", r.stdout)
        self.assertIn("1 件は照合していない", r.stdout)

    def test_capcheck_does_not_change_the_db(self):
        self.new(tid=100)
        db = self.ws / "kanban" / "kanban.db"
        before = db.read_bytes()
        self.kb("capcheck", "--all")
        self.assertEqual(db.read_bytes(), before)


class IntakeTest(unittest.TestCase):
    """LLM の出力を整形した後に、決定的なコードで照合する（LLM には能力を渡さない。ADR-0012）"""

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.tmp = pathlib.Path(d.name)
        self.ws = self.tmp / "ws"; self.ws.mkdir()
        bind = self.tmp / "bin"; bind.mkdir()
        (bind / "claude").write_text(FAKE_CLAUDE, encoding="utf-8"); (bind / "claude").chmod(0o755)
        self.env = {**os.environ, "AIFACTORY_WORKSPACE": str(self.ws), "PATH": f"{bind}:{os.environ['PATH']}"}

    def intake(self, *args):
        return subprocess.run([sys.executable, str(INTAKE), "-", "--pj", "kumitate", "--kind", "bug", *args],
                              input="画面が変です\n", text=True, capture_output=True, env=self.env)

    def test_dry_run_reports_the_warnings_in_its_json(self):
        r = self.intake("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        j = json.loads(r.stdout)
        self.assertEqual(len(j["capability_warnings"]), 2)   # 当たり 1 行 + 但し書き
        self.assertIn("browser", j["capability_warnings"][0])
        self.assertEqual(j["body"], "背景です。\n\n## 完了条件\n- ブラウザで目視して確かめる\n- テストが緑\n")  # 本文は書き換えない

    def test_the_ticket_is_still_created_and_the_warning_goes_to_stderr(self):
        r = self.intake()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[intake] warning:", r.stderr)
        self.assertTrue(list((self.ws / "kanban" / "tickets").glob("*.md")))


if __name__ == "__main__":
    unittest.main()
