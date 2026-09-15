"""lib/aifactory_capabilities.py: 実行環境の「できないこと」（project.yml の `capabilities`）と、票の完了条件の照合。判定はここ 1 か所（チケット 552）。

kb new / intake / console の ticket_new / workflow の run は、この lib を呼ぶだけで、語彙も規則も持たない。

- 宣言の形: project.yml の `capabilities: {browser: bool, docker: bool, egress: bool, gui: bool}`。4 キー固定・全キー任意
- **書いていないキーは「未宣言＝照合しない」**。false（できないと確かめた）と区別する。「読めていない」と「確かめた」を
  混同しないのは ADR-0077 決定 4 と同じ扱い
- 語彙は下の VOCAB（コード側の固定辞書）。PJ ごとに持たない。自由文が相手なので誤検知（例示で触れただけ）と
  取りこぼし（辞書に無い言い回し）は必ず出る。だから **警告だけで、起票も起動も止めない**（ADR-0077 と同じ前提）
- 目的は「実行できないことを可視化して人へ渡す」こと。完了条件を弱める・書き換えるのはこの lib の仕事ではない

pyyaml が無い・project.yml が読めないときは「未宣言」を返す（呼び元を落とさない）。
"""
import pathlib
import re
import sys

CAPS = {
    "browser": "画面のある実ブラウザで目視・操作する",
    "docker": "docker / compose でコンテナを起動する",
    "egress": "外部ネットワークから取得する",
    "gui": "デスクトップ画面を操作する",
}

# 初期語彙はチケット 552 が挙げた実例からのみ組む（推測で増やさない。実データは `kb capcheck` で測ってから足す）。
# `curl` `e2e` のような localhost 用途と衝突する語は入れない。
VOCAB = {
    "browser": ["ブラウザ", "目視", "画面で確認", "画面を見て", "chrome", "safari", "firefox", "playwright", "puppeteer",
                "実 iframe", "実iframe"],
    "docker": ["docker", "compose", "コンテナ"],
    "egress": ["外部から取得", "外部から取り寄せ", "ダウンロードして", "インターネット経由", "外部サイト", "外部サービスに接続",
               "外部サービスへ接続", "外部 API に接続"],
    "gui": ["画面操作", "画面を操作", "クリックして", "スクリーンショット", "デスクトップ"],
}

DONE_HEADING = re.compile(r"^(#{1,6})\s*完了条件\s*$")
HEADING = re.compile(r"^(#{1,6})\s")


def _matcher(term):
    """ASCII の語は語境界で、日本語の語はそのまま。`compose` が `composer` に当たるのを避ける"""
    t = term.lower()
    if re.fullmatch(r"[a-z0-9 _-]+", t):
        return re.compile(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])")
    return re.compile(re.escape(t))


MATCHERS = {cap: [(term, _matcher(term)) for term in terms] for cap, terms in VOCAB.items()}


def declared(project):
    """project.yml の dict → 宣言されている能力だけの {名前: bool}。宣言が無ければ {}。

    `computer_use: true` は gui ができるという実質の宣言なので、`capabilities.gui` が書かれていないときだけ
    そこから導く（他の 3 つは導出しない。分からないものは未宣言のまま残す）。
    """
    if not isinstance(project, dict): return {}
    raw = project.get("capabilities")
    out = {k: bool(v) for k, v in raw.items() if k in CAPS and isinstance(v, bool)} if isinstance(raw, dict) else {}
    if "gui" not in out and project.get("computer_use") is True: out["gui"] = True
    return out


def load_declared(pj):
    """PJ 名 → 宣言されている能力（読めなければ {}＝未宣言。呼び元は落とさない）"""
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import aifactory_paths as paths
        import yaml
        d = paths.project_dir(pj)
        if not d: return {}
        py = d / "project.yml"
        if not py.is_file(): return {}
        with open(py, encoding="utf-8") as f:
            return declared(yaml.safe_load(f))
    except Exception:
        return {}


def done_section(body):
    """本文の `## 完了条件` 節（見出しから同じ深さ以上の次の見出しまで）を [(行番号, 行)] と scope で返す。

    節が無い本文は全体を返し、scope を "body"（= 完了条件の節が無いので全体を見た）にする。
    """
    lines = (body or "").splitlines()
    start = level = None
    for i, line in enumerate(lines):
        m = DONE_HEADING.match(line.strip())
        if m: start, level = i + 1, len(m.group(1)); break
    if start is None:
        return [(i + 1, l) for i, l in enumerate(lines)], "body"
    out = []
    for i in range(start, len(lines)):
        m = HEADING.match(lines[i])
        if m and len(m.group(1)) <= level: break
        out.append((i + 1, lines[i]))
    return out, "完了条件"


def scan(body, decl):
    """本文 × 宣言 → 警告の材料 {"scope": ..., "hits": [{"cap","line","text","terms"}]}。

    宣言が false の能力だけを見る（true は問題無し、未宣言は照合しない）。
    """
    off = sorted(c for c in CAPS if decl.get(c) is False)
    lines, scope = done_section(body)
    hits = []
    if not off: return {"scope": scope, "hits": hits, "off": off}
    for no, line in lines:
        low = line.lower()
        if not low.strip(): continue
        for cap in off:
            terms = [t for t, rx in MATCHERS[cap] if rx.search(low)]
            if terms: hits.append({"cap": cap, "line": no, "text": line.strip(), "terms": terms})
    return {"scope": scope, "hits": hits, "off": off}


def format_warnings(result):
    """scan() の結果 → 人が読む警告の行（0 件なら []）。ブロックはしない、という但し書きを最後に 1 行付ける"""
    hits = result.get("hits") or []
    if not hits: return []
    out = []
    for h in hits:
        out.append(f"{h['line']} 行目: この環境に {h['cap']} は無い（{CAPS[h['cap']]}）と宣言されているのに"
                   f"「{'」「'.join(h['terms'])}」を求めている: {h['text'][:120]}")
    if result.get("scope") == "body":
        out.append("（本文に `## 完了条件` の節が無いので全体を走査した）")
    out.append("機械の語句照合なので外れもある。条件を消さずに、実行者と手段を票に書き足すか、人へ渡すこと")
    return out


def prompt_section(decl, result):
    """依頼文に足す「## この環境で検証できないこと」節（宣言が無い・false が 1 つも無いときは ""）。

    節の目的は run に「できないことをできたことにさせない」こと。完了条件を削らせるためではない。
    """
    off = sorted(c for c in CAPS if decl.get(c) is False)
    if not off: return ""
    lines = ["## この環境で検証できないこと", "",
             "この PJ の定義（project.yml の `capabilities`）が、この実行環境には次が無いと宣言している。"]
    lines += [f"- **{c}**: {CAPS[c]}" for c in off]
    hits = result.get("hits") or []
    if hits:
        lines += ["", "チケットの完了条件のうち、この宣言と食い違う行（機械の語句照合なので外れもある。鵜呑みにしない）:"]
        lines += [f"- {h['line']} 行目（{h['cap']}）: {h['text'][:200]}" for h in hits]
    lines += ["",
              "守ること:",
              "- 実施できないことを実施したことにしない。ゲートが全部緑でも、この条件を満たした証拠にはならない",
              "- 完了条件を削らない・弱めない（書き換えは範囲外）。できないものはできないまま残す",
              "- `report.md` の `## 未検証項目` に、その条件を**文面のまま**列挙し、なぜこの環境で確かめられないかを書く",
              ""]
    return "\n".join(lines)
