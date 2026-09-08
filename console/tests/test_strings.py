"""console の文言（static/strings.js の T）の検査。console/UX.md の約束を機械的に弾く（textlint の代わり。標準ライブラリだけ）。

  python3 -m unittest discover -s console/tests -p 'test_strings.py' -v

- strings.js の本体は厳密な JSON として読めること
- 表記: 補助動詞はひらがな（下さい / 出来る / 頂く）、二重敬語（〜させていただく / 〜いたします）、責める言葉（不正 / 無効）、
  開発者の語彙（セッション / バリデーション / フェッチ / レコード）、用語のゆれ（タスク / プロジェクト）、感嘆符・絵文字・全角英数字を使わない
- ボタン（btn.* とダイアログの ok）は動詞で終わる（「実行する」）。「OK」「はい」「いいえ」は使わない。取り消しは「キャンセル」
- 文（msg / err / empty / help / sub / next / banner とダイアログの本文）は「ですます」で「。」か「？」で終わる。言い切り（〜無い。/ 〜要る。）は弾く
- app.js / index.html が参照する鍵（T.a.b / data-t="a.b"）が strings.js にあり、strings.js の鍵が全部どこかで使われている
- kind.* が kit の種別を全部持ち、本文欄の雛形に「## 完了条件」の箇条書きがある（起票画面で用途と書き方が分かる）
"""
import json, pathlib, re, unittest

STATIC = pathlib.Path(__file__).resolve().parents[1] / "static"
WORKFLOWS = pathlib.Path(__file__).resolve().parents[2] / "workflow" / "kit" / "workflows"
SENTENCE_GROUPS = ("msg", "err", "empty", "help", "sub", "next", "banner", "kind", "stepDesc")
DIALOG_LABEL_KEYS = ("title",)            # ダイアログの中で文でない鍵（ok はボタンとして検査）
FORBIDDEN = {
    "下さい": "補助動詞はひらがな（ください）", "出来": "ひらがな（できる）", "頂": "補助動詞はひらがな（いただく）", "致し": "二重敬語の温床（します）",
    "させていただ": "過剰な丁寧さ（します）", "いたします": "過剰な丁寧さ（します）", "不正": "責める言葉（正しくありません）", "無効": "責める言葉（使えません）",
    "失敗しました": "責める言葉（できませんでした）", "エラーが発生": "情報量ゼロ（何が起きて何をすればよいか）",
    "セッション": "開発者の語彙", "バリデーション": "開発者の語彙", "フェッチ": "開発者の語彙", "レコード": "開発者の語彙",
    "タスク": "用語集: チケット", "プロジェクト": "用語集: PJ", "!": "感嘆符は使わない", "！": "感嘆符は使わない",
}
TERSE_END = re.compile(r"(ない|無い|要る|ある|いる|する|(?<!まし)(?<!でし)た|だ|べき)[。]?$")
VERB_END = tuple("るすくつうむぶぐぬ")


def load():
    src = (STATIC / "strings.js").read_text(encoding="utf-8")
    body = src[src.index("const T = ") + len("const T = "): src.rindex("};") + 1]
    return json.loads(body)


def leaves(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items(): yield from leaves(v, f"{prefix}.{k}" if prefix else k)
    else:
        yield prefix, obj


class StringsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.T = load()
        cls.items = list(leaves(cls.T))

    def test_json_and_shape(self):
        self.assertGreater(len(self.items), 100)
        for path, v in self.items: self.assertIsInstance(v, str, path)
        for k in ("status", "jobState", "nav", "btn", "th", "label", "msg", "err", "empty", "help", "dialog", "next"): self.assertIn(k, self.T)

    def test_forbidden_words_and_characters(self):
        bad = []
        for path, v in self.items:
            for w, why in FORBIDDEN.items():
                if w in v: bad.append(f"{path}: 「{w}」 → {why}")
            if re.search(r"[０-９Ａ-Ｚａ-ｚ　]", v): bad.append(f"{path}: 全角の英数字・空白")
            if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", v): bad.append(f"{path}: 絵文字")
            if re.search(r"[ぁ-んァ-ン一-龥]", v) and re.search(r"[()]", v): bad.append(f"{path}: 日本語の文に半角括弧。全角「（）」にする")
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_buttons_are_verbs(self):
        bad = []
        buttons = [(p, v) for p, v in self.items if p.startswith("btn.") or (p.startswith("dialog.") and p.endswith(".ok"))]
        self.assertGreater(len(buttons), 20)
        for path, v in buttons:
            if v in ("OK", "はい", "いいえ", "実行", "送信", "確認"): bad.append(f"{path}: 中身のないボタン「{v}」")
            elif v != "キャンセル" and not v.rstrip("）").endswith(VERB_END) and not re.search(r"[るすくつうむぶぐぬ]（[^）]*）$", v): bad.append(f"{path}: 動詞で終わっていない「{v}」")
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_sentences_are_polite_and_end_with_period(self):
        bad = []
        for path, v in self.items:
            top = path.split(".")[0]
            is_sentence = top in SENTENCE_GROUPS or (top == "dialog" and path.split(".")[-1] not in DIALOG_LABEL_KEYS + ("ok", "typeToConfirm", "nextIs"))
            if not is_sentence or not v: continue
            if not v.endswith(("。", "？", "：", ":")): bad.append(f"{path}: 文は「。」で終える「{v}」")
            if TERSE_END.search(v.rstrip("。")): bad.append(f"{path}: 言い切りではなく「ですます」で「{v}」")
            if "。" in v.rstrip("。") and re.search(r"[^。]{90,}", v): bad.append(f"{path}: 一文が長い（一文一情報）「{v}」")
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_keys_match_app_js_and_index_html(self):
        app = (STATIC / "app.js").read_text(encoding="utf-8"); html = (STATIC / "index.html").read_text(encoding="utf-8")
        used = set(re.findall(r"\bT\.([A-Za-z_][\w.]*)", app)) | set(re.findall(r'data-t="([\w.]+)"', html))
        used = {u.rstrip(".") for u in used}
        # 参照された鍵が存在する（T.status[s] のような動的参照は親までで良い）
        missing = []
        for u in sorted(used):
            node = self.T
            for k in u.split("."):
                node = node.get(k) if isinstance(node, dict) else None
                if node is None: missing.append(u); break
        self.assertEqual(missing, [], f"app.js / index.html が参照しているが strings.js に無い: {missing}")
        # strings.js の葉が全部使われている（親が動的に参照されていれば子も使われている扱い）
        unused = [p for p, _ in self.items if not any(p == u or p.startswith(u + ".") for u in used)]
        self.assertEqual(unused, [], f"strings.js にあるが使われていない: {unused}")
        # app.js に日本語の直書きが残っていない（文言は strings.js に集める約束）。コメント行は除く
        code = re.sub(r"/\*.*?\*/", "", app, flags=re.S)                         # ブロックコメント
        code = "\n".join(re.sub(r"(?<![:'\"`])//.*$", "", l) for l in code.splitlines())   # 行コメント（https:// は残す）
        inline = re.findall(r"[ぁ-んァ-ン一-龥]{2,}", code)
        self.assertEqual(inline, [], f"app.js に日本語の直書き: {inline[:10]}")

    def test_kind_covers_every_workflow(self):
        """種別の用途は kind.* が正本。kit に種別が増えたらここが落ちて追記を促す"""
        kinds = {f.stem for f in WORKFLOWS.glob("*.yml") if not f.name.startswith((".", "_"))}
        self.assertGreater(len(kinds), 3, WORKFLOWS)
        self.assertEqual(sorted(kinds - set(self.T["kind"])), [], "kind.* に説明の無い種別がある")

    def test_step_desc_covers_every_step(self):
        """実行記録の「各工程が何をするか」は stepDesc.* が正本。kit に工程が増えたらここが落ちて追記を促す"""
        import yaml
        ids = set()
        for f in WORKFLOWS.glob("*.yml"):
            if f.name.startswith((".", "_")): continue
            ids |= {s["id"] for s in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("steps", [])}
        self.assertGreater(len(ids), 5, WORKFLOWS)
        self.assertEqual(sorted(ids - set(self.T["stepDesc"])), [], "stepDesc.* に説明の無い工程がある")

    def test_body_placeholder_shows_the_shape(self):
        v = self.T["label"]["bodyPlaceholder"]
        self.assertIn("## 完了条件", v)
        self.assertIn("- [ ]", v)


if __name__ == "__main__":
    unittest.main()
