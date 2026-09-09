"""intake の `--attach`（チケット 378）。起票したチケットに添付し、画像は LLM にも見せる。

  python3 -m unittest discover -s workflow/tests -v

VM も本物の claude も使わない。PATH の先頭に偽 `claude` を置き、受け取った引数を `$ARGS_OUT` に落とさせる。
偽 claude は `--tools Read` で呼ばれたとき、cwd の `attachments/` を実際に読んで本文の「## 現状」に名前を書く
（本物の Read の代わり。intake が画像を「見せられる形」で渡せているかだけを見る）。
"""
import json, os, pathlib, shutil, subprocess, sys, tempfile, unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
INTAKE = REPO / "glue" / "bin" / "intake"
KB = REPO / "kanban" / "bin" / "kb"
PJ = "kumitate"   # examples/projects/kumitate

sys.path.insert(0, str(REPO / "lib"))
import aifactory_attachments as att   # noqa: E402

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452")   # png の頭だけ（中身は読まない。名前で画像と判る）

FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, os, pathlib, sys
argv = sys.argv[1:]
pathlib.Path(os.environ["ARGS_OUT"]).write_text(json.dumps(argv, ensure_ascii=False), encoding="utf-8")
now = ""
d = pathlib.Path("attachments")
if d.is_dir():           # --tools Read で呼ばれたときだけ、本物の Read の代わりに中身を数える
    now = "\n## 現状\n" + "".join(f"- 画像 {p.name} に赤い枠が写っています\n" for p in sorted(d.iterdir()))
body = "背景です。" + now + "\n## 完了条件\n- 直っている\n"
print("```json\n" + json.dumps({"pj": "kumitate", "kind": "bug", "title": "fix: 画面が変", "body": body,
                                "confidence": 0.9, "reason": "テスト"}, ensure_ascii=False) + "\n```")
"""


class IntakeAttachTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.tmp = pathlib.Path(d.name)
        self.ws = self.tmp / "ws"; self.ws.mkdir()
        bind = self.tmp / "bin"; bind.mkdir()
        (bind / "claude").write_text(FAKE_CLAUDE, encoding="utf-8"); (bind / "claude").chmod(0o755)
        self.args_out = self.tmp / "args.json"
        self.env = {**os.environ, "AIFACTORY_WORKSPACE": str(self.ws), "ARGS_OUT": str(self.args_out),
                    "PATH": f"{bind}:{os.environ['PATH']}"}
        att.paths.ATTACHMENTS = self.ws / "kanban" / "attachments"

    def src(self, name, data=PNG):
        p = self.tmp / "src" / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); return p

    def intake(self, *args, text="画面が変です。直してほしい。\n"):
        return subprocess.run([sys.executable, str(INTAKE), "-", "--pj", PJ, "--kind", "bug", *map(str, args)],
                              input=text, text=True, capture_output=True, env=self.env)

    def claude_args(self):
        return json.loads(self.args_out.read_text(encoding="utf-8"))

    def attachments_of(self, tid):
        r = subprocess.run([sys.executable, str(KB), "attachments", str(tid), "--json"], text=True, capture_output=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [x["name"] for x in json.loads(r.stdout)]

    def test_image_is_shown_to_the_llm_and_attached_to_the_ticket(self):
        r = self.intake("--attach", self.src("画面.png"))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        args = self.claude_args()
        self.assertEqual(args[args.index("--tools") + 1], "Read")      # 画像があるときだけ Read を許す
        self.assertIn("attachments/画面.png", args[1])                  # prompt に相対パスで案内する
        tid = int(r.stdout.split()[0])
        self.assertEqual(self.attachments_of(tid), ["画面.png"])
        body = (self.ws / "kanban" / "tickets").glob(f"{tid}-*.md")
        text = next(body).read_text(encoding="utf-8")
        self.assertIn("## 現状", text)
        self.assertIn("画像 画面.png", text)                            # LLM が画像から書いた事実が本文に入る

    def test_without_images_the_call_is_unchanged(self):
        """画像が無いときは今までどおりツールを一切渡さない（既存の動きを変えない）"""
        r = self.intake("--attach", self.src("表.csv", b"a,b\n"))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        args = self.claude_args()
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertNotIn("## 添付（画像）", args[1])
        self.assertEqual(self.attachments_of(int(r.stdout.split()[0])), ["表.csv"])

    def test_several_files_are_all_attached(self):
        r = self.intake("--attach", self.src("a.png"), self.src("b.png"), "--attach", self.src("c.txt", b"x"))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.attachments_of(int(r.stdout.split()[0])), ["a.png", "b.png", "c.txt"])

    def test_dry_run_reads_the_image_but_does_not_file_or_attach(self):
        r = self.intake("--attach", self.src("画面.png"), "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["pj"], PJ)               # 判定 JSON だけ
        self.assertEqual(self.claude_args()[self.claude_args().index("--tools") + 1], "Read")
        self.assertFalse((self.ws / "kanban" / "tickets").exists() and list((self.ws / "kanban" / "tickets").glob("*.md")))

    def test_missing_attachment_is_refused_before_calling_the_llm(self):
        r = self.intake("--attach", self.tmp / "ない.png")
        self.assertEqual(r.returncode, 1)
        self.assertIn("添付が見つからない", r.stderr)
        self.assertFalse(self.args_out.exists())                       # LLM を呼ぶ前に断る

    def test_filed_but_not_attached_prints_the_id_and_exits_2(self):
        """kb new は「起票はできた・添付だけ失敗」で id を出して rc≠0。id が取れているなら記録は残し、rc 2 で知らせる"""
        big = self.src("大きい.png", b"0" * (att.MAX_FILE + 1))
        r = self.intake("--attach", big)
        self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
        tid = int(r.stdout.split()[0])                                  # 画面はここから id を拾える
        self.assertIn("起票はできたが添付できなかった", r.stderr)
        self.assertIn(f"kb attach {tid}", r.stderr)
        self.assertEqual(self.attachments_of(tid), [])
        self.assertIn(f"\t{tid}\t", (self.ws / "logs" / "intake.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
