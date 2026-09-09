"""チケットの添付（画像・PDF など）を workspace に置き、runner が VM に運んで依頼文で案内する（チケット 353）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。lib は一時ディレクトリ、kb は一時 workspace の子プロセス、runner は
`test_resume_from_step.py` と同じ流儀の偽 VM（`sb` はローカル bash、`scp_to` はコピー、`sandbox take` だけ差し替え）。

- lib: 名前の sanitize（パス区切り・`..`・制御文字）、同じ名前の衝突、1 ファイル / 合計の上限
- kb: attach / attachments / detach / new --attach、show の末尾の一覧、history、添付が無いチケットは今までどおり
- runner: take で `<work>/attachments/` に載り、state.json に残り、依頼文に案内が 1 行入る。添付が無ければ何も足さない
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
KB = REPO / "kanban" / "bin" / "kb"
PJ = "kumitate"   # examples/projects/kumitate

sys.path.insert(0, str(REPO / "lib"))
import aifactory_attachments as att   # noqa: E402

spec = importlib.util.spec_from_loader("attachments_run", importlib.machinery.SourceFileLoader("attachments_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
run.paths = types.SimpleNamespace(project_dir=run.paths.project_dir, PROJECT_DIRS=run.paths.PROJECT_DIRS, RUNS=None)

TICKET = "# 機能: 画面のここを直す\n\n添付のスクリーンショットの赤枠の所。\n"

FAKE_CLAUDE = r"""#!/bin/bash
echo '{"type":"system","subtype":"init","model":"fake-claude","tools":[],"cwd":"'"$PWD"'"}'
printf '# 実装報告: 直した\n' > "$WORK/report.md"
printf 'kumitate\n直した\n' > README.md
echo "書いた"
exit 0
"""


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if p.returncode: raise AssertionError(f"git {' '.join(args)} @ {cwd}: {p.stdout}{p.stderr}")
    return p.stdout


class AttachmentsLibTest(unittest.TestCase):
    """lib/aifactory_attachments.py: 名前の sanitize と上限（判定はここ 1 か所）"""

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.tmp = pathlib.Path(d.name)
        self.addCleanup(setattr, att, "paths", att.paths)
        att.paths = types.SimpleNamespace(ATTACHMENTS=self.tmp / "attachments")

    def src(self, name, data=b"png"):
        p = self.tmp / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); return p

    def test_sanitize_drops_paths_and_control_characters(self):
        self.assertEqual(att.sanitize("../../x.png"), "x.png")
        self.assertEqual(att.sanitize("a/b\\c.png"), "c.png")
        self.assertEqual(att.sanitize("ス\x00ク\x1fリーン.png"), "スクリーン.png")
        self.assertEqual(att.sanitize(".."), "file")
        self.assertEqual(att.sanitize("."), "file")
        self.assertEqual(att.sanitize("   "), "file")
        self.assertEqual(att.sanitize("/tmp/"), "file")
        self.assertEqual(att.sanitize(".ssh"), "_ssh")          # 隠しファイルにしない
        self.assertEqual(att.sanitize("  a.png  "), "a.png")
        # 255 バイトに切っても UTF-8 として壊れない
        long = att.sanitize("あ" * 200 + ".png")
        self.assertLessEqual(len(long.encode("utf-8")), att.MAX_NAME_BYTES)

    def test_add_sanitizes_and_never_escapes_the_ticket_directory(self):
        saved = att.add(101, self.src("shot.png"), name="../../../etc/passwd")
        self.assertEqual(saved, "passwd")
        self.assertTrue((att.dir_for(101) / "passwd").is_file())
        self.assertFalse((self.tmp / "attachments" / "etc").exists())

    def test_same_name_gets_a_suffix_instead_of_overwriting(self):
        self.assertEqual(att.add(101, self.src("a/shot.png", b"1")), "shot.png")
        self.assertEqual(att.add(101, self.src("b/shot.png", b"22")), "shot-2.png")
        self.assertEqual(att.add(101, self.src("c/shot.png", b"333")), "shot-3.png")
        self.assertEqual(att.add(101, self.src("d/README", b"x")), "README")
        self.assertEqual(att.add(101, self.src("e/README", b"x")), "README-2")
        self.assertEqual([i["name"] for i in att.listing(101)], ["README", "README-2", "shot-2.png", "shot-3.png", "shot.png"])
        self.assertEqual([i["size"] for i in att.listing(101) if i["name"] == "shot-3.png"], [3])

    def test_listing_has_name_size_type_and_added(self):
        att.add(102, self.src("表.csv", b"a,b\n"))
        att.add(102, self.src("spec.pdf", b"%PDF"))
        att.add(102, self.src("何か.bin", b"\x00\x01"))
        by = {i["name"]: i for i in att.listing(102)}
        self.assertEqual(by["表.csv"]["type"], "text/csv")
        self.assertEqual(by["spec.pdf"]["type"], "application/pdf")
        self.assertEqual(by["何か.bin"]["type"], att.DEFAULT_TYPE)
        self.assertEqual(by["表.csv"]["size"], 4)
        self.assertRegex(by["表.csv"]["added"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")   # ADR-0026

    def test_ticket_id_is_normalised_and_never_a_path(self):
        self.assertEqual(att.dir_for(101), att.dir_for("101"))
        self.assertEqual(att.dir_for("0101").name, "101")
        self.assertEqual(att.dir_for("../etc").name, "etc")        # 数字でない task-id でも落とさず外にも出ない
        self.assertEqual(att.listing("../etc"), [])

    def test_listing_is_empty_for_a_ticket_without_attachments(self):
        self.assertEqual(att.listing(999), [])
        self.assertEqual(att.total_size(999), 0)

    def test_file_over_the_per_file_limit_is_refused(self):
        big = self.tmp / "big.bin"; big.touch(); os.truncate(big, att.MAX_FILE + 1)   # 疎ファイル（実際には書かない）
        with self.assertRaises(ValueError) as e: att.add(103, big)
        self.assertIn("20 MiB", str(e.exception))
        self.assertEqual(att.listing(103), [])

    def test_total_over_the_ticket_limit_is_refused(self):
        d = att.dir_for(104); d.mkdir(parents=True)
        already = d / "already.bin"; already.touch(); os.truncate(already, att.MAX_TOTAL)
        with self.assertRaises(ValueError) as e: att.add(104, self.src("one.png", b"x"))
        self.assertIn("100 MiB", str(e.exception))
        self.assertEqual([i["name"] for i in att.listing(104)], ["already.bin"])

    def test_directories_and_missing_files_are_refused(self):
        with self.assertRaises(ValueError): att.add(105, self.tmp)
        with self.assertRaises(ValueError): att.add(105, self.tmp / "無い.png")

    def test_remove_only_takes_a_plain_name(self):
        att.add(106, self.src("shot.png"))
        self.assertFalse(att.remove(106, "../106/shot.png"))
        self.assertFalse(att.remove(106, "無い.png"))
        self.assertTrue((att.dir_for(106) / "shot.png").is_file())
        self.assertTrue(att.remove(106, "shot.png"))
        self.assertFalse(att.dir_for(106).exists())     # 空になった置き場は残さない

    def test_add_bytes_takes_the_content_directly(self):
        saved = att.add_bytes(107, "../shot.png", b"\x89PNG")
        self.assertEqual(saved, "shot.png")
        self.assertEqual((att.dir_for(107) / "shot.png").read_bytes(), b"\x89PNG")
        with self.assertRaises(ValueError): att.add_bytes(107, "big.bin", b"x" * (att.MAX_FILE + 1))


class KbAttachTest(unittest.TestCase):
    """kb attach / attachments / detach / new --attach（一時 workspace の子プロセス）"""

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.env = {**os.environ, "AIFACTORY_WORKSPACE": str(self.ws)}
        self.shot = self.ws / "shot.png"; self.shot.write_bytes(b"\x89PNG" + b"0" * 100)
        self.spec = self.ws / "spec.pdf"; self.spec.write_bytes(b"%PDF-1.7")

    def kb(self, *args, check=True, stdin=""):
        p = subprocess.run([sys.executable, str(KB), *args], input=stdin, text=True, capture_output=True, env=self.env)
        if check: self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def new(self, *extra):
        out = self.kb("new", PJ, "feature", "画面のここを直す", "--body", "-", *extra, stdin="本文\n").stdout
        return int(out.split()[0])

    def attachments_dir(self, tid):
        return self.ws / "kanban" / "attachments" / str(tid)

    def test_attach_copies_the_file_and_records_history(self):
        tid = self.new()
        self.kb("attach", str(tid), str(self.shot), str(self.spec))
        self.assertEqual((self.attachments_dir(tid) / "shot.png").read_bytes(), self.shot.read_bytes())
        self.assertTrue(self.shot.exists())                                   # 元は消さない（コピー）
        items = json.loads(self.kb("attachments", str(tid), "--json").stdout)
        self.assertEqual([i["name"] for i in items], ["shot.png", "spec.pdf"])
        self.assertEqual(items[0]["type"], "image/png")
        self.assertEqual(items[0]["size"], self.shot.stat().st_size)
        hist = self.kb("history", str(tid)).stdout
        self.assertIn("attachment", hist)
        self.assertIn("add shot.png", hist)
        # 本文には添付のことを書かない（正本は attachments/。表示側が導く）
        body = next(self.ws.joinpath("kanban", "tickets").glob(f"{tid}-*.md")).read_text(encoding="utf-8")
        self.assertNotIn("添付", body)

    def test_show_lists_attachments_at_the_end_and_stays_the_same_without_them(self):
        tid = self.new()
        before = self.kb("show", str(tid)).stdout
        self.assertNotIn("添付", before)
        self.kb("attach", str(tid), str(self.shot))
        after = self.kb("show", str(tid)).stdout
        self.assertTrue(after.startswith(before.split("updated")[0]))     # 上（メタと本文）は今までどおり
        self.assertIn("添付 (1)", after)
        self.assertIn("shot.png", after)
        self.assertIn(str(self.attachments_dir(tid)), after)

    def test_detach_removes_the_file_and_leaves_history(self):
        tid = self.new()
        self.kb("attach", str(tid), str(self.shot))
        self.kb("detach", str(tid), "shot.png")
        self.assertFalse((self.attachments_dir(tid) / "shot.png").exists())
        self.assertIn("(添付なし)", self.kb("attachments", str(tid)).stdout)
        self.assertIn("shot.png → removed", self.kb("history", str(tid)).stdout)

    def test_detach_of_something_that_is_not_there_fails(self):
        tid = self.new()
        p = self.kb("detach", str(tid), "無い.png", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("無い.png", p.stderr)

    def test_new_can_attach_at_the_same_time(self):
        tid = self.new("--attach", str(self.shot), "--attach", str(self.spec))
        self.assertEqual([i["name"] for i in json.loads(self.kb("attachments", str(tid), "--json").stdout)],
                         ["shot.png", "spec.pdf"])

    def test_attach_stops_at_the_first_failure_and_keeps_what_went_in(self):
        tid = self.new()
        p = self.kb("attach", str(tid), str(self.shot), str(self.ws / "無い.png"), str(self.spec), check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("添付できない", p.stderr)
        self.assertEqual([i["name"] for i in json.loads(self.kb("attachments", str(tid), "--json").stdout)], ["shot.png"])

    def test_other_commands_are_unchanged(self):
        tid = self.new()
        self.kb("attach", str(tid), str(self.shot))
        self.assertIn("画面のここを直す", self.kb("list").stdout)
        self.assertIn("画面のここを直す", self.kb("next").stdout)
        self.kb("start", str(tid)); self.kb("done", str(tid))
        self.assertTrue((self.attachments_dir(tid) / "shot.png").exists())   # 状態を進めても添付は残る


class RunnerAttachmentsTest(unittest.TestCase):
    """runner: take で VM に置き、state.json に残し、依頼文に案内を 1 行入れる"""

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        run.paths.RUNS = self.ws / "runs"
        self.addCleanup(setattr, run.attachments, "paths", run.attachments.paths)
        run.attachments.paths = types.SimpleNamespace(ATTACHMENTS=self.ws / "attachments")
        self.ticket = self.ws / "ticket.md"; self.ticket.write_text(TICKET, encoding="utf-8")
        self.origin = self.ws / "origin.git"
        git(self.ws, "init", "-q", "--bare", "-b", "develop", str(self.origin))
        seed = self.ws / "seed"; seed.mkdir()
        git(seed, "init", "-q", "-b", "develop")
        self.identity(seed)
        (seed / "README.md").write_text("kumitate\n", encoding="utf-8")
        git(seed, "add", "README.md"); git(seed, "commit", "-q", "-m", "初期")
        git(seed, "remote", "add", "origin", str(self.origin))
        git(seed, "push", "-q", "-u", "origin", "develop")
        self.app = self.ws / "app"
        git(self.ws, "clone", "-q", str(self.origin), str(self.app))
        self.identity(self.app)

    def identity(self, d):
        git(d, "config", "user.name", "aifactory test")
        git(d, "config", "user.email", "test@example.invalid")

    def attach(self, task, name, data=b"\x89PNG"):
        src = self.ws / f"src-{name}"; src.write_bytes(data)
        return run.attachments.add(task, src, name=name)

    def fake_take(self):
        real_sh = run.sh
        def fake_sh(cmd, check=True, capture=True, input_text=None, env=None):
            if isinstance(cmd, list) and cmd[:2] == ["sandbox", "take"]:
                return subprocess.CompletedProcess(cmd, 0, f"task-{cmd[3]} ready\n", "")
            return real_sh(cmd, check=check, capture=capture, input_text=input_text, env=env)
        run.sh = fake_sh
        self.addCleanup(lambda: setattr(run, "sh", real_sh))

    def build(self, task, **kw):
        r = run.Run(PJ, str(task), "feature", str(self.ticket), **kw)
        work = self.ws / f"work-{task}"; work.mkdir(exist_ok=True)
        r.work = str(work)
        env = {"SANDBOX_APP_DIR": str(self.app), "WORK": str(work)}

        def sb(cmd, input_text=None, check=True):
            p = subprocess.run(["bash", "-c", cmd], text=True, capture_output=True, input=input_text,
                               env={**os.environ, **env})
            if check and p.returncode:
                raise RuntimeError(f"command failed ({p.returncode}): {cmd}\n{p.stderr[-500:]}")
            return p.stdout

        r.sb = sb
        r.prepare = lambda: None
        r.release = lambda: None
        r.refresh_token = lambda: None
        r.scp_to = lambda local, remote: shutil.copy(str(local), str(remote))
        # 本物の scp と同じ約束で偽装する: 宛先はディレクトリで、名前はローカルの basename がそのまま付く
        # （宛先に名前を書く実装に戻ると self.scp_dests の assert で落ちる）
        self.scp_dests = []

        def scp_files_to(locals, remote_dir):
            self.scp_dests.append(str(remote_dir))
            for p in locals:
                shutil.copy(str(p), os.path.join(str(remote_dir), os.path.basename(str(p))))

        r.scp_files_to = scp_files_to
        self.work = work
        return r

    def prompt_of(self, r):
        return r.build_prompt(r.steps[r.wf["steps"][0]["id"]])

    def test_take_puts_the_attachments_on_the_vm_and_records_them(self):
        self.fake_take()
        self.attach(950, "shot.png")
        self.attach(950, "spec.pdf", b"%PDF-1.7")
        r = self.build(950)
        r.take()
        self.assertEqual(sorted(p.name for p in (self.work / "attachments").iterdir()), ["shot.png", "spec.pdf"])
        self.assertEqual((self.work / "attachments" / "shot.png").read_bytes(), b"\x89PNG")
        state = json.loads((r.run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual([a["name"] for a in state["attachments"]], ["shot.png", "spec.pdf"])
        self.assertEqual(state["attachments"][0]["type"], "image/png")

    def test_names_with_spaces_and_japanese_arrive_unchanged(self):
        """scp の宛先に利用者由来の名前を書かないこと（書くと OpenSSH 9 以降の既定＝SFTP で
        quote した記号がそのまま名前になり、逆に quote しないとレガシーで空白入りが壊れる。353 のレビュー指摘）"""
        self.fake_take()
        for n in ("画面 1.png", "a b.png", "plain.png"):
            self.attach(955, n)
        r = self.build(955)
        r.take()
        self.assertEqual(sorted(p.name for p in (self.work / "attachments").iterdir()),
                         ["a b.png", "plain.png", "画面 1.png"])
        self.assertEqual(self.scp_dests, [f"{r.work}/attachments/"])   # 宛先はディレクトリだけ
        self.assertIn("画面 1.png", self.prompt_of(r))

    def test_the_prompt_points_at_the_attachments(self):
        self.fake_take()
        self.attach(951, "画面.png")
        r = self.build(951)
        r.take()
        prompt = self.prompt_of(r)
        guide = f"- 添付: {r.work}/attachments/（画面.png。"
        self.assertIn(guide, prompt)
        self.assertIn("Read で開いて見ること", prompt)
        # 案内は「## チケット」の後（本文を読んでから添付を見る順）
        self.assertLess(prompt.index("## チケット"), prompt.index(guide))

    def test_a_ticket_without_attachments_is_unchanged(self):
        self.fake_take()
        r = self.build(952)
        r.take()
        self.assertFalse((self.work / "attachments").exists())
        state = json.loads((r.run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn("attachments", state)
        self.assertNotIn(f"- 添付: {r.work}/", self.prompt_of(r))

    def test_dry_run_shows_the_attachments_without_touching_a_vm(self):
        self.attach(953, "shot.png")
        r = self.build(953, dry=True)
        r.take()
        self.assertFalse((self.work / "attachments").exists())    # 転送はしない
        self.assertIn(f"- 添付: {r.work}/attachments/（shot.png。", self.prompt_of(r))
        self.assertEqual([a["name"] for a in r.state["attachments"]], ["shot.png"])

    def test_resume_keeps_what_the_first_run_recorded(self):
        self.fake_take()
        self.attach(954, "shot.png")
        r = self.build(954)
        r.take()
        os.environ["AIFACTORY_RESUME_RUN"] = r.run_dir.name
        self.addCleanup(os.environ.pop, "AIFACTORY_RESUME_RUN", None)
        again = self.build(954, resume=True)
        again.take()                                              # 貸出中の VM に運び直さない
        self.assertEqual([a["name"] for a in again.state["attachments"]], ["shot.png"])
        self.assertIn(f"- 添付: {again.work}/attachments/（shot.png。", self.prompt_of(again))


if __name__ == "__main__":
    unittest.main()
