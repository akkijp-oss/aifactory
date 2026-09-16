"""VM の work/ の成果物（report.md / plan.md / research.md / 画像 / gates/*.log）を runs/<run>/work/ へ回収する（チケット 557）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。一時 dir を「VM の中」に見立て、PATH の先頭に偽の `sandbox` を置いて
**本物の** `Run.collect_artifacts()` / `Run.release()` を回す（偽 `sandbox ssh` は受け取ったコマンド中の
`/home/dev` を `$VMROOT` に読み替えて bash に渡すだけなので、列挙の `find` も取得の `tar` も本物が動く）。
VM への口は他の test と同じ `sandbox ssh` 1 本に揃える（`ssh` を直に呼ぶと、PATH の偽 `sandbox` で VM を
模している他の test が本物の ssh を掴んで timeout ぶん止まる）。

見るのはチケットの完了条件そのもの:
- `work/` 直下の `*.md` と画像、`gates/` と `attachments/` の中身が **同じ相対パス・同じバイト列**で届く
- 上限（1 ファイル / 合計 / 件数）に当たったものは黙って落とさず `state.json["artifacts_skipped"]` に理由が残る
- 回収が失敗しても例外を外に出さず `sandbox release` まで進む（VM を塞がない）
- 「work/ が元から無い（自死した run）」と「在るのに届かない」を別の記録として区別する
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import tarfile
import tempfile
import time
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_loader("artifacts_run", importlib.machinery.SourceFileLoader("artifacts_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

# 偽 sandbox。`ssh` は受け取ったコマンド中の `/home/dev` を `$VMROOT` に読み替えて bash に渡すだけなので、
# 列挙の `find` も取得の `tar` も本物が動く。`release` 等はログに残すだけ（VM を返したかを test が見る）
FAKE_SANDBOX = r"""#!/usr/bin/env bash
if [ "$1" = ssh ]; then
  if [ -n "$FAKE_SSH_RC" ]; then echo "ssh: connect to host 10.77.1.1 port 22: Connection refused" >&2; exit "$FAKE_SSH_RC"; fi
  shift 2; exec bash -c "${1//\/home\/dev/$VMROOT}"
fi
echo "$@" >> "$VMROOT/sandbox.log"
"""


class ReleaseArtifactsTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.vm = self.ws / "vm"; self.vm.mkdir()
        self.bin = self.ws / "bin"; self.bin.mkdir()
        for name, body in (("sandbox", FAKE_SANDBOX),):
            f = self.bin / name; f.write_text(body, encoding="utf-8"); f.chmod(0o755)
        old = {k: os.environ.get(k) for k in ("PATH", "VMROOT", "FAKE_SSH_RC")}
        os.environ.update(VMROOT=str(self.vm), PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        os.environ.pop("FAKE_SSH_RC", None)
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None)
                                 for k, v in old.items()])

    def limit(self, name, value):
        """上限の定数を一時的に下げる（32 MiB を実際に作らずに合計上限の挙動を見るため）"""
        old = getattr(run, name)
        setattr(run, name, value)
        self.addCleanup(lambda: setattr(run, name, old))

    # ---------- 偽 VM と Run
    def work(self, task="900"):
        d = self.vm / "work" / task
        d.mkdir(parents=True, exist_ok=True)
        return d

    def make_run(self, task="900", keep=False):
        """`Run.__init__` は PJ 定義・チケット・VM を要求するので、回収に要る属性だけ持つ Run を作る。
        見たいのは release / collect_artifacts の中身だけで、take や workflow の解釈は別の test が見ている"""
        r = run.Run.__new__(run.Run)
        r.dry, r.keep, r.pj, r.task, r.t0 = False, keep, "artf", task, time.time()
        r.work = f"/home/dev/work/{task}"
        r.run_dir = self.ws / "runs" / f"2026-09-16-artf-{task}"
        r.run_dir.mkdir(parents=True, exist_ok=True)
        r.state_file = r.run_dir / "state.json"
        r.state = {"pj": "artf", "task": task}
        r.vm_ip = lambda: "10.77.1.1"
        return r

    def state(self, r):
        return json.loads(r.state_file.read_text(encoding="utf-8"))

    def skipped(self, r):
        return {s["name"]: s["reason"] for s in self.state(r).get("artifacts_skipped", [])}

    def released(self):
        p = self.vm / "sandbox.log"
        return p.read_text(encoding="utf-8").split() if p.is_file() else []

    # ---------- 完了条件 1/2: 直下の *.md と画像、gates/ と attachments/ が同じ深さで届く
    def test_markdown_images_and_subdirs_are_collected_at_the_same_path(self):
        w = self.work()
        (w / "report.md").write_text("# 実装報告\n## 未検証項目\n無し\n", encoding="utf-8")
        (w / "plan.md").write_text("# 計画\n", encoding="utf-8")
        (w / "research.md").write_text("# 調査\n", encoding="utf-8")
        (w / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
        (w / "結果 メモ.md").write_text("空白と日本語の名前\n", encoding="utf-8")   # scp に名前を並べない理由（quote 問題）の担保
        (w / "gates").mkdir(); (w / "gates" / "test.log").write_text("FAIL test\n", encoding="utf-8")
        (w / "attachments").mkdir(); (w / "attachments" / "a.png").write_bytes(b"\x89PNG-attach")
        r = self.make_run()
        r.release()
        dest = r.run_dir / "work"
        self.assertEqual((dest / "report.md").read_text(encoding="utf-8"), "# 実装報告\n## 未検証項目\n無し\n")
        self.assertEqual((dest / "plan.md").read_bytes(), (w / "plan.md").read_bytes())
        self.assertEqual((dest / "research.md").read_bytes(), (w / "research.md").read_bytes())
        self.assertEqual((dest / "shot.png").read_bytes(), (w / "shot.png").read_bytes())
        self.assertEqual((dest / "結果 メモ.md").read_bytes(), (w / "結果 メモ.md").read_bytes())
        self.assertEqual((dest / "gates" / "test.log").read_text(encoding="utf-8"), "FAIL test\n")
        self.assertEqual((dest / "attachments" / "a.png").read_bytes(), b"\x89PNG-attach")
        st = self.state(r)
        self.assertTrue(st["artifacts_received"])
        self.assertEqual(st["artifacts_count"], 7)
        self.assertEqual(st["artifacts_bytes"], sum(f.stat().st_size for f in (w.rglob("*")) if f.is_file()))
        self.assertNotIn("artifacts_skipped", st)
        self.assertNotIn("artifacts_error", st)
        self.assertEqual(self.released(), ["release", "900"])

    # ---------- 完了条件 4: 回収できないものは黙って落とさず理由が残る
    def test_symlink_other_directory_and_oversize_file_are_skipped_with_a_reason(self):
        w = self.work()
        (w / "report.md").write_text("報告\n", encoding="utf-8")
        (w / "link.md").symlink_to(w / "report.md")
        (w / "shots").mkdir(); (w / "shots" / "deep.png").write_bytes(b"png")
        with open(w / "big.png", "wb") as f: f.truncate(run.ARTIFACT_FILE_MAX + 1)
        r = self.make_run()
        r.release()
        dest = r.run_dir / "work"
        self.assertEqual((dest / "report.md").read_text(encoding="utf-8"), "報告\n")
        self.assertFalse((dest / "link.md").exists())
        self.assertFalse((dest / "shots").exists())
        self.assertFalse((dest / "big.png").exists())
        self.assertEqual(self.skipped(r), {"link.md": "symlink", "shots": "directory", "big.png": "size"})
        st = self.state(r)
        self.assertTrue(st["artifacts_received"])      # 「対象外がある」は回収の失敗ではない
        self.assertEqual(st["artifacts_count"], 1)

    def test_total_limit_keeps_the_reports_and_records_what_was_dropped(self):
        self.limit("ARTIFACT_TOTAL_MAX", 100)
        w = self.work()
        (w / "report.md").write_bytes(b"r" * 60)
        (w / "big1.png").write_bytes(b"1" * 60)
        (w / "big2.png").write_bytes(b"2" * 60)
        r = self.make_run()
        r.release()
        dest = r.run_dir / "work"
        self.assertEqual((dest / "report.md").read_bytes(), b"r" * 60)   # *.md が先（優先順）
        self.assertFalse((dest / "big1.png").exists())
        self.assertEqual(self.skipped(r), {"big1.png": "total", "big2.png": "total"})
        self.assertTrue(self.state(r)["artifacts_received"])   # 「対象外がある」は回収の失敗ではない

    def test_count_limit_records_the_rest(self):
        self.limit("ARTIFACT_COUNT_MAX", 2)
        w = self.work()
        for n in ("a.md", "b.md", "c.png", "d.png"): (w / n).write_bytes(n.encode())
        r = self.make_run()
        r.release()
        self.assertEqual(self.state(r)["artifacts_count"], 2)
        self.assertEqual(self.skipped(r), {"c.png": "count", "d.png": "count"})

    # ---------- 回収に失敗しても VM は返す（例外を外に出さない）
    def test_ssh_failure_is_recorded_and_the_vm_is_still_released(self):
        self.work()
        r = self.make_run()
        os.environ["FAKE_SSH_RC"] = "255"
        r.release()
        st = self.state(r)
        self.assertFalse(st["artifacts_received"])
        self.assertIn("rc=255", st["artifacts_error"])
        self.assertEqual(self.released(), ["release", "900"])

    # ---------- 「元から無い」と「在るのに届かない」を分ける（自死した run の report.md）
    def test_missing_work_dir_is_recorded_as_no_work_dir(self):
        r = self.make_run()                     # VM 側に work/<task>/ を作らない
        r.release()
        st = self.state(r)
        self.assertFalse(st["artifacts_received"])
        self.assertEqual(st["artifacts_error"], "no work dir")
        self.assertEqual(self.released(), ["release", "900"])

    def test_listed_but_not_delivered_is_recorded_as_missing(self):
        w = self.work()
        (w / "report.md").write_text("報告\n", encoding="utf-8")
        (w / "plan.md").write_text("計画\n", encoding="utf-8")
        r = self.make_run()
        real = r.ssh_bytes

        def half(cmd, stdin=b"", timeout=120):
            # 取得の tar だけ report.md を落として返す（列挙にはあるのに届かなかった、を作る）
            if "tar -cf -" in cmd: stdin = b"plan.md\0"
            return real(cmd, stdin, timeout)

        r.ssh_bytes = half
        r.release()
        st = self.state(r)
        self.assertFalse(st["artifacts_received"])
        self.assertEqual(self.skipped(r), {"report.md": "missing"})
        self.assertEqual((r.run_dir / "work" / "plan.md").read_text(encoding="utf-8"), "計画\n")

    # ---------- run_dir/work が既にあっても 1 段ネストしない（`scp -r` の宛先規約を踏まない）
    def test_existing_work_dir_does_not_nest_the_artifacts(self):
        w = self.work()
        (w / "report.md").write_text("報告\n", encoding="utf-8")
        r = self.make_run()
        (r.run_dir / "work").mkdir()
        (r.run_dir / "work" / "review.md").write_text("前の回の残り\n", encoding="utf-8")
        r.release()
        self.assertEqual((r.run_dir / "work" / "report.md").read_text(encoding="utf-8"), "報告\n")
        self.assertFalse((r.run_dir / "work" / "900").exists())
        self.assertTrue((r.run_dir / "work" / "review.md").is_file())

    def test_keep_collects_but_does_not_release(self):
        w = self.work()
        (w / "report.md").write_text("報告\n", encoding="utf-8")
        r = self.make_run(keep=True)
        r.release()
        self.assertTrue((r.run_dir / "work" / "report.md").is_file())
        self.assertEqual(self.released(), [])

    # ---------- tar の中身は信用しない（VM の agent が名前を作れる）
    def test_extract_drops_members_outside_the_accepted_names(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for name in ("report.md", "../escape.md", "/tmp/absolute.md", "sneaky.md"):
                data = b"x"
                info = tarfile.TarInfo(name); info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            link = tarfile.TarInfo("plan.md"); link.type = tarfile.SYMTYPE; link.linkname = "/etc/passwd"
            tf.addfile(link)
        dest = self.ws / "dest"; dest.mkdir()
        written, unsafe = run.extract_artifacts(buf.getvalue(), dest, ["report.md", "plan.md"])
        self.assertEqual(written, {"report.md": 1})
        self.assertEqual(sorted(u["name"] for u in unsafe), ["../escape.md", "/tmp/absolute.md", "plan.md", "sneaky.md"])
        self.assertEqual(sorted(p.name for p in dest.rglob("*")), ["report.md"])
        self.assertFalse((self.ws / "escape.md").exists())

    # ---------- 選別そのもの（純関数）
    def test_select_prefers_reports_then_gate_logs_then_attachments_then_images(self):
        accepted, skipped = run.select_artifacts([
            ("f", 1, b"z.png"), ("f", 1, b"attachments/a.pdf"), ("f", 1, b"gates/test.log"),
            ("f", 1, b"report.md"), ("f", 1, b"other.bin"), ("d", 4096, b"gates"), ("d", 4096, b"attachments")])
        self.assertEqual(accepted, ["report.md", "gates/test.log", "attachments/a.pdf", "z.png", "other.bin"])
        self.assertEqual(skipped, [])            # 入れ物の gates / attachments 自体は skip 扱いにしない

    def test_select_rejects_control_characters_in_names(self):
        accepted, skipped = run.select_artifacts([("f", 1, b"ok.md"), ("f", 1, "\u58ca\nれた.md".encode())])
        self.assertEqual(accepted, ["ok.md"])
        self.assertEqual(skipped, [{"name": "壊?れた.md", "reason": "name", "size": 1}])   # ログと記録に改行を差し込ませない

    def test_select_rejects_unsafe_and_undecodable_names(self):
        accepted, skipped = run.select_artifacts([("f", 1, b"../x.md"), ("f", 1, b"/etc/passwd"), ("f", 1, b"\xff.md"), ("p", 0, b"fifo")])
        self.assertEqual(accepted, [])
        self.assertEqual({s["name"]: s["reason"] for s in skipped},
                         {"../x.md": "name", "/etc/passwd": "name", "\ufffd.md": "name", "fifo": "non-regular"})


if __name__ == "__main__":
    unittest.main()
