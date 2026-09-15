"""read_file / ticket_attach_path が「見つかりません」と言うとき、その場所に実在する名前を併せて返すこと（#550）。

一時ディレクトリだけで自立している（VM も claude も kb も使わない）。

  python3 -m unittest discover -s console/tests -v
"""
import importlib.util, pathlib, tempfile, unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("notfound_core", ROOT / "console/lib/core.py")
core = importlib.util.module_from_spec(spec); spec.loader.exec_module(core)

SECRET = "これは中身なので応答に出てはいけない"


class ReadFileMissingTest(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="aifactory-notfound-"); self.addCleanup(td.cleanup)
        self.tmp = pathlib.Path(td.name).resolve()
        self.runs = self.tmp / "runs"
        self.run = self.runs / "2026-09-15-example-550"
        (self.run / "work").mkdir(parents=True)
        (self.run / "agent-plan-0.log").write_text(SECRET + "\n", encoding="utf-8")
        (self.run / "state.json").write_text("{}\n", encoding="utf-8")
        (self.run / ".hidden").write_text("x\n", encoding="utf-8")
        (self.run / "work" / "plan.md").write_text("# 計画\n", encoding="utf-8")
        p = patch.object(core, "READ_ROOTS", [self.runs]); p.start(); self.addCleanup(p.stop)

    def err_of(self, path):
        d, err = core.read_file(str(path))
        self.assertIsNone(d, f"読めてしまった: {path}")
        return err

    def test_missing_file_lists_the_run(self):
        """run はあるがファイル名が違う → その run 直下の実在名が付く（名前だけ。中身は出さない）"""
        err = self.err_of(self.run / "agent-implement-0.log")
        self.assertTrue(err.startswith("ファイルが見つかりません: "), err)
        self.assertIn("agent-implement-0.log", err)              # 何が無かったのかを名指しする
        for name in ("agent-plan-0.log", "state.json", "work/"):  # ディレクトリは末尾 /
            self.assertIn(name, err)
        self.assertIn("3 件", err)                                # `.hidden` は数にも入らない
        self.assertNotIn(".hidden", err)
        self.assertNotIn(SECRET, err)

    def test_missing_run_is_distinguishable(self):
        """run 自体が無い → 「ディレクトリが見つかりません」で始まり、runs/ 直下の実在 run 名が付く（完了条件 2）"""
        err = self.err_of(self.runs / "2026-09-15-example-999" / "agent-plan-0.log")
        self.assertTrue(err.startswith("ディレクトリが見つかりません: "), err)
        self.assertIn("2026-09-15-example-999", err)
        self.assertIn("2026-09-15-example-550/", err)             # 在る run の名前
        self.assertNotIn("agent-plan-0.log", err)                 # 列挙するのは欠けた要素の親の直下だけ（再帰しない）

    def test_deep_missing_names_the_first_missing_element(self):
        """途中の work/gates/ が無い → 最初に欠けた要素を名指しし、その親（work/）の直下を並べる"""
        err = self.err_of(self.run / "work" / "gates" / "oss-check.log")
        self.assertTrue(err.startswith("ディレクトリが見つかりません: "), err)
        self.assertIn(str(self.run / "work" / "gates"), err)
        self.assertIn("にあるのは 1 件: plan.md", err)

    def test_long_listing_is_capped(self):
        d = self.runs / "2026-09-15-example-551"
        d.mkdir(parents=True)
        for i in range(50): (d / f"f-{i:02d}.jsonl").write_text("{}\n", encoding="utf-8")
        (d / ".secret").write_text("x\n", encoding="utf-8")
        err = self.err_of(d / "state.json")
        self.assertIn("にあるのは 50 件: ", err)
        self.assertIn("（ほか 10 件）", err)
        self.assertIn("f-39.jsonl", err)                          # 先頭 40 件（名前順）
        self.assertNotIn("f-40.jsonl", err)
        self.assertNotIn(".secret", err)

    def test_empty_directory_says_so(self):
        d = self.runs / "2026-09-15-example-552"
        d.mkdir(parents=True)
        err = self.err_of(d / "state.json")
        self.assertTrue(err.startswith("ファイルが見つかりません: "), err)
        self.assertTrue(err.endswith("は空です"), err)

    def test_outside_roots_is_unchanged(self):
        """許可域の外は従来どおりの文言のまま（読める場所を広げないので一覧も付けない）"""
        err = self.err_of(self.tmp / "soto" / "nai.log")
        self.assertIn("この場所のファイルは表示できません", err)
        self.assertNotIn("にあるのは", err)

    def test_listing_stops_at_the_allowed_root(self):
        """許可された根そのものが無いときは列挙しない（根の外へ遡らない）"""
        empty = self.tmp / "nai-runs"
        self.assertEqual(core.not_found_message(empty / "r" / "a.log", root=empty),
                         f"ディレクトリが見つかりません: {empty}")

    def test_the_root_itself_is_not_listed_from_outside(self):
        """要求パスが根そのもの（実在するディレクトリ）でも、根の親は列挙しない（遡りの検査を素通りしない）"""
        err = self.err_of(self.runs)                        # runs/ は在るがファイルではない
        self.assertTrue(err.startswith("ファイルが見つかりません: "), err)
        self.assertNotIn("にあるのは", err)                   # 根の親（tmp 直下）の名前を出さない
        # 添付側の根（/tmp・ホーム）でも同じ。/ 直下の名前が出ない
        self.assertNotIn("にあるのは", core.not_found_message(pathlib.Path("/tmp"), root=pathlib.Path("/tmp")))

    def test_attach_path_missing_lists_siblings(self):
        """ticket_attach_path（ApiError を投げる口）も同じ導出を使う。404 のまま文言だけが厚くなる"""
        with patch.object(core, "ATTACH_PATH_ROOTS", [self.tmp]):
            with self.assertRaises(core.ApiError) as cm:
                core.ticket_attach_path(1, str(self.run / "gamen.png"))
        self.assertEqual(cm.exception.code, 404)
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("ファイルが見つかりません: "), msg)
        self.assertIn("agent-plan-0.log", msg)
        self.assertNotIn(SECRET, msg)


if __name__ == "__main__":
    unittest.main()
