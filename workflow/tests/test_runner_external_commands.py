"""runner が呼ぶ外部コマンドの名前を、テストが偽装している集合に閉じ込める（チケット 615）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。読むのはソースだけ（構文木）。

## 守る不変条件

`workflow/tests` の多くは **PATH の先頭に偽の実行ファイルを置いて VM を模す**（偽 `sandbox` / `scp` / `claude` / `gh`）。
偽装は **名前で**効くので、`workflow/bin/run` が今まで呼んでいなかった名前の外部コマンドを呼び始めた瞬間、
**その呼び出しだけが本物に抜ける**。VM を模しているテストの宛先は到達しないので、本物は timeout いっぱい待つ。

2026-09-16、PR #164（票 #557）がこれを踏んだ: 追加された `Run.ssh_bytes` だけが `ssh` を直に呼び、
`release()` を通るテストが本物の `ssh` を掴んで 120 秒ずつ待ち、**CI の test job が 21 分で打ち切られた**
（`The operation was canceled.`）。★この穴は**新しいテスト自身では絶対に落ちない**（`test_release_artifacts` は
`ssh_bytes` を Python レベルで stub しているので緑）。落ちるのは他人のテストで、しかも「assertion の失敗」ではなく
「全体が遅い」という形でしか出ない。だから review も gates も通った。

そこで構文木を読み、runner が `subprocess` / `sh()` / `stream()` に渡す **argv[0] のリテラル**を集め、
許可リストに無い名前が増えたら名指しで落とす。

★**この検査は「名前の集合を閉じる」だけ**で、「その呼び出しが正しいか」は判定しない（#578 の反省: 字面の一律禁止は
将来の誤検知で検査ごと捨てられる）。同じ理由で、**リテラルで取れない argv[0]（変数・f-string・式）は FAIL にせず
件数だけ出す**。静的解析で全部は取れないので、取れない分を赤にすると検査自体が信用されなくなって黙って無効化される。
"""
import ast
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
RUNNER = REPO / "workflow" / "bin" / "run"
TESTS = REPO / "workflow" / "tests"
# 票が「lib も対象に含めるか確かめる」と言うので含める。linux.py / windows.py は subprocess を直接呼ばず
# （VM 操作は QEMU guest-agent の RPC 経由）、macos.py の 2 件は argv[0] が式なので「取れなかった」に落ちる。
TARGETS = [RUNNER] + sorted((REPO / "workflow" / "lib").glob("*.py"))

# 外部コマンドを起こす口。sh() / stream() は runner 内の薄い包み（workflow/bin/run:68 / :181）
SUBPROCESS_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}
WRAPPERS = {"sh", "stream"}

# 許可リスト = runner が argv[0] として呼んでよい名前。実測（構文木）で作る。
#   sandbox … 偽 sandbox を PATH へ置くテストが多数（test_prepare / test_clock_skew / test_gates_base_red ほか）
#   scp     … 同じテスト群が偽 scp を置く（宛先 dev@<ip>:<path> をローカルへ写す）
#   gh      … test_gh_token_from_app / test_macos が偽 gh を置く
#   bash    … kit/steps/*.sh を**ローカルで**実行する口（run:1250）。VM に到達しないので偽装せず本物を使わせている
ALLOWED = {"sandbox", "scp", "gh", "bash"}
# うち「テスト側が PATH で偽装していること」まで求める名前（bash は上記のとおり意図的に本物）
MUST_BE_FAKED = ALLOWED - {"bash"}

# 退行注入の差し込み位置（VM helper の直前）
ANCHOR = "    def sb(self, cmd, input_text=None, check=True):"


def _argv0(node):
    """呼び出しノードの argv[0]。(名前, None) か (None, 取れなかった理由) を返す。"""
    if not node.args:
        return None, "引数なし"
    a = node.args[0]
    if isinstance(a, (ast.List, ast.Tuple)):
        if not a.elts:
            return None, "空のリスト"
        head = a.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            # basename は掛けない。絶対パス指定は PATH 偽装の対象外なので「取れなかった」側に落とす
            return (head.value, None) if "/" not in head.value else (None, f"絶対/相対パス {head.value!r}")
        return None, f"先頭要素が {type(head).__name__}"
    if isinstance(a, ast.Constant) and isinstance(a.value, str):
        # shell=True の文字列コマンド。現状 0 件だが文字列経路を黙って素通しにしない
        words = a.value.split()
        return (words[0], None) if words else (None, "空の文字列コマンド")
    return None, f"argv[0] が {type(a).__name__}"


def external_commands(src):
    """外部コマンドの呼び出しを集める。

    返り値は (found, unresolved):
      found      … {名前: [行番号, ...]}（リテラルで取れたもの）
      unresolved … [(行番号, 理由), ...]（取れなかったもの。★FAIL の材料にはしない）
    """
    found, unresolved = {}, []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in SUBPROCESS_FUNCS \
                and isinstance(f.value, ast.Name) and f.value.id == "subprocess":
            pass
        elif isinstance(f, ast.Name) and f.id in WRAPPERS:
            pass
        else:
            continue
        name, why = _argv0(node)
        if name is None:
            unresolved.append((node.lineno, why))
        else:
            found.setdefault(name, []).append(node.lineno)
    return found, unresolved


def scan_targets():
    """TARGETS 全部を走査して (found{名前: [(ファイル名, 行)]}, unresolved[(ファイル名, 行, 理由)]) を返す"""
    found, unresolved = {}, []
    for path in TARGETS:
        f, u = external_commands(path.read_text(encoding="utf-8"))
        for name, lines in f.items():
            found.setdefault(name, []).extend((path.name, ln) for ln in lines)
        unresolved.extend((path.name, ln, why) for ln, why in u)
    return found, unresolved


# --- 許可リストの導出（2 つの表が食い違わないように、テスト側の実態から取り直す） -------------------
def _scope_faked(scope):
    """1 つのスコープ（関数 or モジュール直下）で PATH へ書き出している実行ファイル名"""
    names, assigned, chmodded = set(), {}, set()
    inner = [n for n in ast.walk(scope) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not scope]
    skip = {id(x) for fn in inner for x in ast.walk(fn)}          # 入れ子の関数は自分のスコープで見る
    for n in ast.walk(scope):
        if id(n) in skip:
            continue
        # (i) `for name, body in (("sandbox", FAKE_SANDBOX), ("scp", FAKE_SCP)):` の並び
        if isinstance(n, ast.Tuple) and len(n.elts) == 2:
            a, b = n.elts
            if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                    and isinstance(b, ast.Name) and b.id.startswith("FAKE_"):
                names.add(a.value)
        # (ii) `p = bin_dir / "claude"` … `p.chmod(0o755)` / `(bind / "claude").chmod(0o755)`
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v = n.value
            if isinstance(v, ast.BinOp) and isinstance(v.op, ast.Div) \
                    and isinstance(v.right, ast.Constant) and isinstance(v.right.value, str):
                assigned.setdefault(n.targets[0].id, set()).add(v.right.value)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "chmod":
            r = n.func.value
            if isinstance(r, ast.Name):
                chmodded.add(r.id)
            elif isinstance(r, ast.BinOp) and isinstance(r.op, ast.Div) \
                    and isinstance(r.right, ast.Constant) and isinstance(r.right.value, str):
                names.add(r.right.value)
    for c in chmodded:
        names |= assigned.get(c, set())
    return names


def faked_names():
    """runner を起動するテストが PATH へ置いている偽の実行ファイル名（実測）。

    対象は「ソースに workflow/bin/run が出てくるテスト」だけ。ctl 更新 / install.sh / sandbox のクロックの
    テスト（FAKE_GIT / FAKE_CURL / FAKE_SUDO / FAKE_SYSTEMCTL ほか）は別スクリプトの偽装なので自然に外れる。
    """
    names = set()
    for path in sorted(TESTS.glob("test_*.py")):
        src = path.read_text(encoding="utf-8")
        if "workflow/bin/run" not in src and '"workflow" / "bin" / "run"' not in src:
            continue
        tree = ast.parse(src)
        for scope in [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            names |= _scope_faked(scope)
    return names


def complaint(name, where):
    """なぜ駄目か + 次の一手。★FAIL の文言はここ 1 か所で作る"""
    at = "、".join(f"{f} 行 {ln}" for f, ln in where)
    return (f'{at}: "{name}" はテストが偽装していない名前。PATH の偽物で VM を模すテスト'
            "（test_prepare / test_macos / test_linux / test_windows / test_clock_skew / test_attachments /"
            " test_gates_base_red / test_resume_start / test_resume_from_step / test_sync_base /"
            " test_sweep_exclude / test_agent_quota / test_agent_timeout の 13 件）が本物を掴んで timeout ぶん"
            "止まる（2026-09-16 PR #164 で CI の test job が 21 分打ち切り）。"
            "★次の一手: 既存の口（sb() / run_remote()）を使って sandbox ssh 経由にするか、"
            f"偽装の側（テストの FAKE_* と この検査の ALLOWED）にも \"{name}\" を足すこと。")


def inject(lines):
    """workflow/bin/run の VM helper の直前に数行を差し込んだソースと、差し込んだ先頭の行番号（退行注入用）"""
    src = RUNNER.read_text(encoding="utf-8").splitlines()
    if ANCHOR not in src:
        raise AssertionError(f"run に差し込み位置（{ANCHOR.strip()}）が無い。退行注入の土台を直すこと")
    i = src.index(ANCHOR)
    return "\n".join(src[:i] + lines + src[i:]) + "\n", i + 1


DIRECT_SSH = [                                    # #557 が足したのと同じ型の呼び出し（PATH 偽装をすり抜ける）
    "    def _leak(self):",
    '        return subprocess.run(["ssh", "dev@host.example.invalid", "true"], capture_output=True)',
]
INNOCENT = [                                      # 許可済みの名前 + argv[0] がリテラルで取れない呼び出し
    "    def _ok(self, cmd_from_variable):",
    '        sh(["sandbox", "ssh", self.task, "true"])',
    "        return subprocess.run(cmd_from_variable, capture_output=True)",
]


class RunnerExternalCommandsTest(unittest.TestCase):
    def test_runner_calls_only_faked_names(self):
        """runner の argv[0] が、テストが偽装している名前の集合に閉じていること（本体）"""
        found, _ = scan_targets()
        bad = sorted(set(found) - ALLOWED)
        self.assertEqual(bad, [], "\n" + "\n".join(complaint(n, found[n]) for n in bad))

    def test_current_calls_are_seen(self):
        """積極側: 現に呼んでいる名前が実際に見えていること（検査が空振りで緑になっていない証明）"""
        found, _ = scan_targets()
        for name in sorted(ALLOWED):
            self.assertIn(name, found, f'"{name}" の呼び出しが 1 つも見えない。検査が空振りしていないか確かめること')

    def test_unresolved_calls_are_counted_not_failed(self):
        """リテラルで取れなかった argv[0] は FAIL にせず件数を出す（黙って見逃さないことが要件）"""
        _, unresolved = scan_targets()
        detail = "; ".join(f"{f}:{ln} {why}" for f, ln, why in unresolved)
        print(f"\n[615] argv[0] がリテラルで取れなかった呼び出し: {len(unresolved)} 件（{detail}）")
        # ★件数を固定値で assert しない。sh() の書き換えで落ちる検査は信用されなくなって捨てられる
        self.assertIsInstance(len(unresolved), int)

    def test_allowlist_matches_what_tests_fake(self):
        """許可リストとテスト側の偽装の突き合わせ（2 つの表が食い違わないように）。

        向きは「許可しているのに誰も偽装していない名前」を落とす側。テストが偽装していても runner が
        argv[0] として呼ばない名前（claude … 偽 sandbox の ssh 分岐がゲスト内で起こす）は余っていてよい。
        """
        faked = faked_names()
        self.assertTrue(faked, "偽装している名前が 1 つも取れない。faked_names() の導出が壊れている")
        missing = sorted(MUST_BE_FAKED - faked)
        self.assertEqual(missing, [], f"許可リストにあるのに、どのテストも PATH で偽装していない: {missing}")

    def test_direct_ssh_is_caught_by_name(self):
        """退行注入（その 1）: ssh を直に呼ぶ 1 行を足すと、行番号つきで名指しで落ちる"""
        src, line = inject(DIRECT_SSH)
        found, _ = external_commands(src)
        self.assertIn("ssh", found, "ssh を直に呼んだのに検査が落ちない")
        self.assertIn(line + 1, found["ssh"])                       # def の次の行
        msg = complaint("ssh", [("run", ln) for ln in found["ssh"]])
        for hint in ('"ssh"', "sb()", "run_remote()", "FAKE_", "ALLOWED", "timeout"):
            self.assertIn(hint, msg)                                # なぜ駄目か + 次の一手が文言にある

    def test_an_allowed_or_unresolved_call_is_not_flagged(self):
        """退行注入（その 2）: 許可済みの名前と、リテラルで取れない argv[0] では落ちない（誤検知を作らない）"""
        src, line = inject(INNOCENT)
        found, unresolved = external_commands(src)
        base, _ = external_commands(RUNNER.read_text(encoding="utf-8"))
        # 見るのは**差し込んだ分が増やした名前**だけ（本体が既に何かで赤でも、この自己検査は独立に判断する）
        self.assertEqual(set(found) - set(base) - ALLOWED, set())
        self.assertIn(line + 1, found["sandbox"])
        self.assertIn(line + 2, [ln for ln, _ in unresolved])       # 変数の argv[0] は「取れなかった」側


if __name__ == "__main__":
    unittest.main()
