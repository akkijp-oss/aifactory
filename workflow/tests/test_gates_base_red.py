"""gates が赤のとき、その赤いゲートを base でも回して「環境／既存の赤」か「自分の変更の赤」かを機械で切り分ける（チケット 330）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。一時 dir に bare の origin と clone（= VM の作業コピー）を作り、PATH の先頭に偽の
`sandbox` / `scp` を置いて、**本物の** `kit/steps/gates.sh` と PJ の gates.sh を回す（`test_sync_base.py` と同じで、
偽装するのは VM と agent だけ。git と judge の遷移は実物）。

チケットが挙げた 2 つの事例を両方見る:
- B 型（2026-09-09 の aifactory 自身。base のコミット漏れで `unittest-console` が base でも赤）
  → prepare では直らない。base でも赤いと分かったら INFO に落とし、implement へ戻さず review へ進める
- A 型（kumitate の DB migration。テンプレートから base が進んで環境がずれた）
  → prepare で直る。prepare が無ければ HEAD も base も赤くなり、INFO に落ちて記録に残る
- HEAD だけ赤（自分の変更が壊した）→ FAIL のまま implement へ戻し、作業ツリーの未コミット変更も残す
- base を見た後に作業ブランチへ戻せなかった → 赤が残っていなくても人間へ回し、退避は base ではなく作業ブランチを押す
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_loader("base_red_run", importlib.machinery.SourceFileLoader("base_red_run", str(REPO / "workflow/bin/run")))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
# 置き場だけ一時 dir に向ける（共有の aifactory_paths は書き換えない。同じプロセスで他の test が読んでいる）
run.paths = types.SimpleNamespace(project_dir=run.paths.project_dir, PROJECT_DIRS=run.paths.PROJECT_DIRS, RUNS=None)

TICKET = "# 機能: base でも赤いゲートの切り分け\n\n偽の VM で gates を赤くする。\n"

FAKE_SANDBOX = r"""#!/usr/bin/env bash
case "$1" in
  ssh) shift 2; SANDBOX_APP_DIR="$APP" CLAUDE_CODE_OAUTH_TOKEN_FABLE=fake-token-pool CLAUDE_CODE_OAUTH_TOKEN_OPUS=fake-token-pool CLAUDE_CODE_OAUTH_TOKEN_SONNET=fake-token-pool CLAUDE_CODE_OAUTH_TOKEN_HAIKU=fake-token-pool exec bash -c "$1" ;;
esac
exit 0
"""

FAKE_SCP = r"""#!/usr/bin/env bash
src="${@: -2:1}"; dst="${@: -1}"; dst="${dst#*:}"; dst="$VMROOT${dst#/home/dev}"
mkdir -p "$(dirname "$dst")" && cp "$src" "$dst"
"""

# PJ のゲート。examples/projects/*/gates.sh と同じ契約（引数があればその名前だけ走らせる）
PJ_GATES = r"""#!/usr/bin/env bash
set -uo pipefail
cd "${SANDBOX_APP_DIR:-$HOME/app}"
mkdir -p "$HOME/gates"; rc=0
SELECT="$*"
gate() { local name=$1; shift
  if [ -n "$SELECT" ]; then case " $SELECT " in *" $name "*) ;; *) return 0 ;; esac; fi
  local log="$HOME/gates/$name${GATES_LOG_SUFFIX:-}.log"
  if "$@" > "$log" 2>&1; then echo "PASS $name"; else echo "FAIL $name (~/gates/$name${GATES_LOG_SUFFIX:-}.log)"; rc=1; fi
}
check() { if [ -f "$1" ]; then echo "ok $1"; else echo "missing $1"; return 1; fi; }
gate strings check strings-ok.txt
gate feature check feature-ok.txt
gate env-ready check "$HOME/prepared"
exit $rc
"""

# 出力が大きいゲート（work/gates/<name>.log の上限の効き方を見る）。契約は PJ_GATES と同じ
NOISY_GATES = r"""#!/usr/bin/env bash
set -uo pipefail
cd "${SANDBOX_APP_DIR:-$HOME/app}"
mkdir -p "$HOME/gates"
noisy() { local line; line="$(head -c 2000 /dev/zero | tr '\0' x)"; for i in $(seq 1 400); do echo "$i $line"; done; test -f noisy-ok.txt; }
log="$HOME/gates/noisy${GATES_LOG_SUFFIX:-}.log"
if noisy > "$log" 2>&1; then echo "PASS noisy"; exit 0; fi
echo "FAIL noisy (~/gates/noisy${GATES_LOG_SUFFIX:-}.log)"; exit 1
"""

PROJECT = """name: basered
repo: example/basered
base_branch: develop
app_dir: {app}
gates: gates.sh
"""

PREPARE = """#!/usr/bin/env bash
set -euo pipefail
touch "$HOME/prepared"
echo "[prepare] 環境を base に揃えた"
"""


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if p.returncode: raise AssertionError(f"git {' '.join(args)} @ {cwd}: {p.stdout}{p.stderr}")
    return p.stdout


def git_out(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True).stdout


class GatesBaseRedTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        run.paths.RUNS = self.ws / "runs"
        run.paths.PROJECT_DIRS = [self.ws / "projects"]
        run.paths.project_dir = lambda pj: next((d / pj for d in run.paths.PROJECT_DIRS if (d / pj / "project.yml").is_file()), None)
        self.vm = self.ws / "vm"; self.vm.mkdir()
        self.app = self.vm / "app"
        self.ticket = self.ws / "ticket.md"; self.ticket.write_text(TICKET, encoding="utf-8")
        self.bin = self.ws / "bin"; self.bin.mkdir()
        for name, body in (("sandbox", FAKE_SANDBOX), ("scp", FAKE_SCP)):
            f = self.bin / name; f.write_text(body, encoding="utf-8"); f.chmod(0o755)
        (self.vm / ".config" / "sandbox").mkdir(parents=True)
        (self.vm / ".config" / "sandbox" / "state.json").write_text(
            json.dumps({str(t): {"ip": "10.77.1.1"} for t in range(900, 910)}), encoding="utf-8")
        # 偽 VM の中に居るつもりで動かす（gates.sh は $HOME/.config/sandbox/state.json と ~/gates/ を見る）
        old = {k: os.environ.get(k) for k in ("HOME", "PATH", "APP", "VMROOT")}
        os.environ.update(HOME=str(self.vm), APP=str(self.app), VMROOT=str(self.vm),
                          PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None)
                                 for k, v in old.items()])

    # ---------- 偽 VM の中のリポジトリ
    def make_repo(self, base_files):
        self.origin = self.ws / "origin.git"
        git(self.ws, "init", "-q", "--bare", "-b", "develop", str(self.origin))
        seed = self.ws / "seed"; seed.mkdir()
        git(seed, "init", "-q", "-b", "develop")
        self.identify(seed)
        (seed / "README.md").write_text("basered\n", encoding="utf-8")
        for f in base_files: (seed / f).write_text(f"{f}\n", encoding="utf-8")
        git(seed, "add", "-A"); git(seed, "commit", "-q", "-m", "初期")
        git(seed, "remote", "add", "origin", str(self.origin)); git(seed, "push", "-q", "-u", "origin", "develop")
        git(self.ws, "clone", "-q", str(self.origin), str(self.app))
        self.identify(self.app)

    def identify(self, d):
        git(d, "config", "user.name", "aifactory test"); git(d, "config", "user.email", "test@example.invalid")

    def make_ctl_checkout(self, branch="main", repo="example/basered"):
        """PJ 定義を「ctl の checkout」として作る（チケット 487）。

        本物の ctl は main 追従（ADR-0042）で、gates.sh はそこから VM へ配られる。base が develop の PJ では、
        develop に着地したゲートが昇格まで配られない（#446 の unittest-pull）。その形をそのまま作る:
        origin に main と develop、develop 側の gates.sh にだけ `gate unittest-pull` があり、checkout は main"""
        origin = self.ws / "ctl-origin" / "example" / "basered.git"
        origin.parent.mkdir(parents=True, exist_ok=True)
        git(self.ws, "init", "-q", "--bare", "-b", "main", str(origin))
        seed = self.ws / "ctl-seed"; (seed / "projects" / "basered").mkdir(parents=True)
        git(seed, "init", "-q", "-b", "main"); self.identify(seed)
        d = seed / "projects" / "basered"
        (d / "project.yml").write_text(PROJECT.format(app=self.app).replace("repo: example/basered", f"repo: {repo}"), encoding="utf-8")
        (d / "gates.sh").write_text(PJ_GATES, encoding="utf-8")
        git(seed, "add", "-A"); git(seed, "commit", "-q", "-m", "PJ 定義（main）")
        git(seed, "remote", "add", "origin", str(origin)); git(seed, "push", "-q", "-u", "origin", "main")
        # develop にだけ足したゲートと provision.sh（= 昇格まで配られない分）
        git(seed, "checkout", "-q", "-b", "develop")
        (d / "gates.sh").write_text(PJ_GATES.replace("exit $rc", "gate unittest-pull check pull-ok.txt\nexit $rc"), encoding="utf-8")
        (d / "provision.sh").write_text("#!/usr/bin/env bash\necho provision\n", encoding="utf-8")
        git(seed, "add", "-A"); git(seed, "commit", "-q", "-m", "unittest-pull をゲートに足す")
        git(seed, "push", "-q", "-u", "origin", "develop")
        ctl = self.ws / "ctl"
        git(self.ws, "clone", "-q", "-b", branch, str(origin), str(ctl)); self.identify(ctl)
        run.paths.PROJECT_DIRS = [ctl / "projects"]
        return ctl / "projects" / "basered"

    def make_project(self, prepare=False):
        d = self.ws / "projects" / "basered"; d.mkdir(parents=True)
        y = PROJECT.format(app=self.app)
        if prepare:
            y += "prepare: prepare.sh\n"
            (d / "prepare.sh").write_text(PREPARE, encoding="utf-8")
        (d / "project.yml").write_text(y, encoding="utf-8")
        (d / "gates.sh").write_text(PJ_GATES, encoding="utf-8")
        return d

    # ---------- 偽 VM（sb / run_remote / agent 工程だけ差し替え。gates は本物を回す）
    def build(self, task, implement, prepare=False, ctl=None):
        self.make_ctl_checkout(**ctl) if ctl is not None else self.make_project(prepare=prepare)
        r = run.Run("basered", str(task), "feature", str(self.ticket))
        self.assertEqual(r.base, "develop")
        work = self.ws / f"work-{task}"; work.mkdir()
        r.work = str(work)
        self.done = []; self.gates_n = 0
        app = self.app

        def sb(cmd, input_text=None, check=True):
            p = subprocess.run(["bash", "-c", cmd], text=True, capture_output=True, input=input_text,
                               env={**os.environ, "SANDBOX_APP_DIR": str(app), "CLAUDE_CODE_OAUTH_TOKEN_FABLE": "fake-token-pool", "CLAUDE_CODE_OAUTH_TOKEN_OPUS": "fake-token-pool", "CLAUDE_CODE_OAUTH_TOKEN_SONNET": "fake-token-pool", "CLAUDE_CODE_OAUTH_TOKEN_HAIKU": "fake-token-pool"})   # take が鍵プールから VM に書く系統別の鍵（無いと runner は起動前に failure: key で止まる。ADR-0060）
            if check and p.returncode:
                raise RuntimeError(f"command failed ({p.returncode}): {cmd}\n{p.stderr[-500:]}")
            return p.stdout

        def take():
            git(app, "fetch", "-q", "origin", "develop")
            git(app, "checkout", "-q", "-B", r.branch, "origin/develop")
            r.prepare()                       # project.yml に prepare があれば本物を走らせる

        def run_agent(step, retry_note=""):
            self.done.append(step["id"])
            self.notes = getattr(self, "notes", {}); self.notes.setdefault(step["id"], []).append(retry_note)
            if step["id"] == "implement": implement(len(self.notes["implement"]) - 1)
            return True, ""

        def run_code(step):
            self.done.append(step["id"])
            if step["code"] == "gates.sh":
                self.gates_n += 1
                return run.Run.run_code(r, step)      # ← 本命。kit/steps/gates.sh を本物で回す
            return True, ""

        r.sb, r.take, r.run_agent, r.run_code = sb, take, run_agent, run_code
        r.run_remote = lambda cmd, log_path, render=None, timeout=3600: run.stream(
            ["bash", "-c", cmd], log_path, render=render, env={"SANDBOX_APP_DIR": str(app)})
        r.scp_to = lambda local, remote: shutil.copy(str(local), str(remote))
        r.release = lambda: None
        r.preserve = lambda: ""
        r.refresh_token = lambda: None
        self.r = r
        return r

    def steps(self, r):
        return [h["step"] for h in r.state["history"]]

    def gates_txt(self, r):
        return (pathlib.Path(r.work) / "gates.txt").read_text(encoding="utf-8")

    def gate_log(self, r, name):
        """work/gates/<name>.log（チケット 331）。VM を返した後も残る回収対象。無ければ None"""
        p = pathlib.Path(r.work) / "gates" / f"{name}.log"
        return p.read_text(encoding="utf-8") if p.is_file() else None

    def verdict(self, r):
        """gates.txt の判定の行だけ（`=== ` から先は base の結果とログ末尾。console の gate_fails もそこで読むのをやめる）"""
        out = []
        for line in self.gates_txt(r).splitlines():
            if line.startswith("=== "): break
            out.append(line)
        return "\n".join(out)

    def commit(self, message):
        git(self.app, "add", "-A"); git(self.app, "commit", "-q", "-m", message)

    # ---------- B 型: base のコミット漏れで base でも赤（2026-09-09 の aifactory 自身）
    def test_a_gate_red_on_base_too_is_downgraded_and_does_not_go_back_to_implement(self):
        self.make_repo(["feature-ok.txt"])          # strings-ok.txt が base に無い = base でも strings が赤
        (self.vm / "prepared").touch()
        def implement(n):
            (self.app / "impl.txt").write_text("実装\n", encoding="utf-8"); self.commit("実装した")
        r = self.build(901, implement)
        r.main()
        # 赤は 1 つだけ出たが、base でも赤いので implement へ戻さず review へ進む
        self.assertEqual(self.steps(r), ["research", "design", "implement", "gates", "review", "sync", "pr"])
        self.assertEqual(self.gates_n, 1)
        self.assertTrue(r.state["history"][3]["ok"])
        self.assertEqual(r.state["history"][3]["next"], "review")
        self.assertNotIn("gates->implement", r.state["loops"])
        # 判定の根拠が gates.txt に残る（reviewer と人間が確かめられる）
        txt = self.gates_txt(r)
        self.assertIn("INFO strings red (also red on base; not a gate)", self.verdict(r))
        self.assertNotIn("FAIL", self.verdict(r))       # 判定の行に赤は残らない（base の結果は === から先に残す）
        self.assertIn("PASS feature", txt)
        self.assertIn("=== base check: origin/develop", txt)
        self.assertIn("--- strings.base.log on base (tail 30)", txt)
        self.assertIn("FAIL strings (~/gates/strings.base.log)", txt)   # base 側は別名前空間に書く（#551）
        self.assertNotIn("BASE-CHECK-LOG-MISSING", txt)
        self.assertIn("missing strings-ok.txt", txt)
        self.assertTrue((self.vm / "gates" / "strings.base.log").is_file(), "base 確認の ~/gates/strings.base.log が無い")
        # INFO に落ちた分もログは work/ に残す（「元から赤い」の根拠。ただし依頼文には載せない。チケット 331）
        log = self.gate_log(r, "strings")
        self.assertIsNotNone(log, "INFO に落ちたゲートの work/gates/strings.log が無い")
        self.assertIn("strings.log on HEAD", log)
        self.assertIn("missing strings-ok.txt", log)
        # base でも赤いゲートは run の記録に残る（console の sandbox 画面がここを読む）
        self.assertEqual(r.state["known_red_gates"], ["strings"])
        self.assertEqual((pathlib.Path(r.work) / "base-red.txt").read_text(encoding="utf-8").split(), ["strings"])
        # base を見た後、作業ブランチに戻っている
        self.assertEqual(git_out(self.app, "rev-parse", "--abbrev-ref", "HEAD").strip(), r.branch)

    # ---------- HEAD だけ赤: 自分の変更が壊した分は今までどおり implement へ戻す
    def test_a_gate_red_only_on_head_still_goes_back_to_the_implementer(self):
        self.make_repo(["strings-ok.txt", "feature-ok.txt"])
        (self.vm / "prepared").touch()
        def implement(n):
            if n == 0:
                git(self.app, "rm", "-q", "feature-ok.txt"); self.commit("feature-ok.txt を消した（壊した）")
                # 未コミットの作業（追跡済みの変更と未追跡ファイル）。base を見に行っても消えてはいけない
                (self.app / "README.md").write_text("書きかけ\n", encoding="utf-8")
                (self.app / "scratch.txt").write_text("メモ\n", encoding="utf-8")
            else:
                # 1 回目の gates の直後。赤いゲートのログが work/ に落ちている（チケット 331）
                self.red_log = self.gate_log(self.r, "feature")
                (self.app / "feature-ok.txt").write_text("feature-ok.txt\n", encoding="utf-8"); self.commit("戻した")
        r = self.build(902, implement)
        r.main()
        # 1 回戻して直った
        self.assertEqual(self.steps(r), ["research", "design", "implement", "gates", "implement", "gates", "review", "sync", "pr"])
        self.assertEqual(r.state["loops"]["gates->implement"], 1)
        self.assertEqual(r.state.get("known_red_gates"), None)
        # 戻すときの依頼文に「base で赤い分は INFO に落としてある」と書いてある（実装役に推測させない）
        self.assertIn("FAIL feature", self.notes["implement"][1])
        self.assertIn("also red on base", self.notes["implement"][1])
        # 赤の中身（VM の ~/gates/feature.log）が work/ に残り、依頼文にも抜粋が入る（チケット 331）
        self.assertIsNotNone(self.red_log, "1 回目の gates の後に work/gates/feature.log が無い")
        self.assertIn("missing feature-ok.txt", self.red_log)
        self.assertIn("=== tail 300", self.red_log)
        note = self.notes["implement"][1]
        self.assertIn("work/gates/feature.log", note)
        self.assertIn("missing feature-ok.txt", note)
        self.assertNotIn("### strings", note)           # 赤くないゲートの抜粋は入れない
        # 緑になった回は古いログを持ち越さない（直したのに「まだ赤い」と読まれる）
        self.assertIsNone(self.gate_log(r, "feature"), "全緑の回に前の回の work/gates/feature.log が残っている")
        # 未コミットの作業が stash → pop で戻っている
        self.assertEqual((self.app / "README.md").read_text(encoding="utf-8"), "書きかけ\n")
        self.assertEqual((self.app / "scratch.txt").read_text(encoding="utf-8"), "メモ\n")
        self.assertEqual(git_out(self.app, "stash", "list").strip(), "")
        self.assertEqual(git_out(self.app, "rev-parse", "--abbrev-ref", "HEAD").strip(), r.branch)

    # ---------- BASE-CHECK が本実行の赤いログを上書きしない（チケット 551）
    def test_the_base_check_keeps_the_head_log_and_writes_its_own_base_log(self):
        """診断のための再実行が診断対象を壊してはいけない（run が残す証跡は後続の処理で壊れない）。

        base 確認は同じゲート名で走るので、出力先を分けないと VM の `~/gates/<名前>.log` が base の結果で
        上書きされ、「FAIL なのにログは全緑」という読めない証跡になる（2026-09-14 kumitate #521 の誤診）。
        本実行は `<名前>.log`、base 確認は `<名前>.base.log` に書く"""
        self.make_repo(["strings-ok.txt", "feature-ok.txt"])
        (self.vm / "prepared").touch()
        def implement(n):
            if n == 0:
                git(self.app, "rm", "-q", "feature-ok.txt"); self.commit("feature-ok.txt を消した（壊した）")
            else:
                # 1 周目の gates の直後（2 周目の gates で上書きされる前）に VM の中を覗く
                self.head_log = (self.vm / "gates" / "feature.log").read_text(encoding="utf-8")
                b = self.vm / "gates" / "feature.base.log"
                self.base_log = b.read_text(encoding="utf-8") if b.is_file() else None
                self.red_txt = self.gates_txt(self.r)     # gates.txt は回ごとに上書きされる。赤かった回の分をここで取る
                (self.app / "feature-ok.txt").write_text("feature-ok.txt\n", encoding="utf-8"); self.commit("戻した")
        r = self.build(914, implement)
        r.main()
        # 本実行の赤い中身がそのまま残っている（base の緑で塗り潰されていない）
        self.assertIn("missing feature-ok.txt", self.head_log,
                      f"本実行の ~/gates/feature.log が base の結果で上書きされている: {self.head_log!r}")
        self.assertNotIn("ok feature-ok.txt", self.head_log)
        # base 確認の出力は別ファイルに在る（base では緑）
        self.assertIsNotNone(self.base_log, "base 確認の ~/gates/feature.base.log が無い")
        self.assertIn("ok feature-ok.txt", self.base_log)
        # gates.txt の案内が正しいパスを指す
        txt = self.red_txt
        self.assertIn("=== base check: origin/develop", txt)
        base_block = txt.split("=== base check: origin/develop", 1)[1]
        self.assertIn("BASE-CHECK origin/develop", base_block)
        self.assertIn("~/gates/<name>.base.log", base_block)
        self.assertIn("PASS feature", base_block)
        self.assertNotIn("BASE-CHECK-LOG-MISSING", txt)
        # 判定と抜粋は従来どおり（挙動不変）
        self.assertIn("FAIL feature", self.notes["implement"][1])
        self.assertIn("missing feature-ok.txt", self.notes["implement"][1])
        self.assertEqual(r.state["loops"]["gates->implement"], 1)

    # ---------- PJ の gates.sh が契約を守っていないと 1 行言う（判定は変えない。チケット 551）
    def test_a_pj_gates_that_ignores_the_log_suffix_is_reported(self):
        """workspace/projects の私有 PJ はこの repo から直せない。黙って元の事故（上書き）に戻らないよう、
        base 側のログが出ていない run では gates.txt に 1 行残す。FAIL にはしない（判定不変）"""
        self.make_repo(["strings-ok.txt", "feature-ok.txt"])
        (self.vm / "prepared").touch()
        def implement(n):
            if n == 0:
                git(self.app, "rm", "-q", "feature-ok.txt"); self.commit("feature-ok.txt を消した（壊した）")
            else:
                self.red_txt = self.gates_txt(self.r)
                (self.app / "feature-ok.txt").write_text("feature-ok.txt\n", encoding="utf-8"); self.commit("戻した")
        r = self.build(915, implement)
        # 出力先を固定した古い契約の gates.sh に差し替える
        old = PJ_GATES.replace('${GATES_LOG_SUFFIX:-}', '')
        self.assertNotEqual(old, PJ_GATES, "PJ_GATES が GATES_LOG_SUFFIX を見ていない")
        (self.ws / "projects" / "basered" / "gates.sh").write_text(old, encoding="utf-8")
        r.main()
        self.assertIn("BASE-CHECK-LOG-MISSING feature", self.red_txt)
        # 判定は不変: FAIL のまま implement へ 1 回戻る
        self.assertIn("FAIL feature", self.notes["implement"][1])
        self.assertEqual(r.state["loops"]["gates->implement"], 1)
        self.assertEqual(self.steps(r), ["research", "design", "implement", "gates", "implement", "gates", "review", "sync", "pr"])

    # ---------- examples の gates.sh が契約を守っている（VM 不要）
    def test_example_gates_honor_the_log_suffix(self):
        """`gate()` の契約は kit と PJ の間の取り決め。examples が古い版に戻ると、それを写した PJ が事故に戻る"""
        found = sorted((REPO / "examples" / "projects").glob("*/gates.sh"))
        self.assertTrue(found, "examples/projects/*/gates.sh が無い")
        for f in found:
            line = next((l for l in f.read_text(encoding="utf-8").splitlines()
                         if '"$@" >' in l and "gates/" in l), None)
            self.assertIsNotNone(line, f"{f}: gate() のログ出力行が読めない")
            self.assertIn("GATES_LOG_SUFFIX", line, f"{f}: gate() が GATES_LOG_SUFFIX を見ていない（#551）: {line.strip()}")

    # ---------- A 型: 環境のずれは prepare で直る（kumitate の DB migration）
    def test_an_environment_gate_passes_when_prepare_sets_the_environment_up(self):
        self.make_repo(["strings-ok.txt", "feature-ok.txt"])     # $HOME/prepared は無い = 環境がずれた VM
        def implement(n):
            (self.app / "impl.txt").write_text("実装\n", encoding="utf-8"); self.commit("実装した")
        r = self.build(903, implement, prepare=True)
        r.main()
        self.assertTrue((self.vm / "prepared").exists())
        self.assertEqual(self.steps(r), ["research", "design", "implement", "gates", "review", "sync", "pr"])
        self.assertEqual(self.gates_n, 1)
        self.assertIn("PASS env-ready", self.gates_txt(r))
        self.assertNotIn("base check", self.gates_txt(r))        # 赤が無いので base は見に行かない
        self.assertEqual(r.state.get("known_red_gates"), None)

    # ---------- A 型: prepare が無いと環境の赤が base にも出る（直す先は実装ではないと分かる）
    def test_without_prepare_the_environment_gate_is_red_on_base_too_and_is_recorded(self):
        self.make_repo(["strings-ok.txt", "feature-ok.txt"])
        def implement(n):
            (self.app / "impl.txt").write_text("実装\n", encoding="utf-8"); self.commit("実装した")
        r = self.build(904, implement, prepare=False)
        r.main()
        self.assertEqual(self.steps(r), ["research", "design", "implement", "gates", "review", "sync", "pr"])
        self.assertNotIn("gates->implement", r.state["loops"])
        self.assertIn("INFO env-ready red (also red on base; not a gate)", self.gates_txt(r))
        self.assertEqual(r.state["known_red_gates"], ["env-ready"])

    # ---------- 赤が無くなった回は base の判定を持ち越さない
    def test_a_green_gates_run_clears_the_base_red_note_left_by_the_previous_round(self):
        """base-red.txt は VM に残る。赤が出なかった回に書き直さないと、人が VM を直して --resume で回し直しても
        runner が前の回の結果（名前や !restore-failed）を読んで、また人間へ回してしまう"""
        self.make_repo(["feature-ok.txt"])          # strings は base でも赤
        (self.vm / "prepared").touch()
        def implement(n):
            if n == 0:
                git(self.app, "rm", "-q", "feature-ok.txt"); self.commit("feature-ok.txt を消した（壊した）")
            else:                                   # 自分の赤を直し、base の赤も HEAD 側で埋めた
                (self.app / "feature-ok.txt").write_text("feature-ok.txt\n", encoding="utf-8")
                (self.app / "strings-ok.txt").write_text("strings-ok.txt\n", encoding="utf-8")
                self.commit("戻して strings も足した")
        r = self.build(907, implement)
        r.main()
        self.assertEqual(self.gates_n, 2)
        self.assertEqual(r.state["result"], "human")            # pr まで進んだ（feature.yml の終わり）
        self.assertEqual(self.steps(r)[-4:], ["gates", "review", "sync", "pr"])
        self.assertEqual(r.state["known_red_gates"], ["strings"])   # 1 回目に確かめた分は記録に残る
        # 2 回目（全緑・base を見に行っていない）が VM の base-red.txt を空に書き直している
        self.assertEqual((pathlib.Path(r.work) / "base-red.txt").read_text(encoding="utf-8").strip(), "")
        self.assertFalse(r.base_check_broken)

    # ---------- base を見られなかった回は、実装役への依頼文で言い切らない
    def test_when_the_base_check_is_skipped_the_retry_note_does_not_claim_base_was_checked(self):
        self.make_repo(["strings-ok.txt", "feature-ok.txt"])
        (self.vm / "prepared").touch()
        def implement(n):
            if n == 0:
                git(self.app, "rm", "-q", "feature-ok.txt"); self.commit("feature-ok.txt を消した（壊した）")
                # base が見えない状況（origin から base が消えた・fetch できない）を作る
                git(self.origin, "branch", "-D", "develop")
                git(self.app, "update-ref", "-d", "refs/remotes/origin/develop")
            else:
                (self.app / "feature-ok.txt").write_text("feature-ok.txt\n", encoding="utf-8"); self.commit("戻した")
                git(self.app, "push", "-q", "origin", f"{r.branch}:develop")   # 後の工程のために base を戻す
                git(self.app, "fetch", "-q", "origin", "develop")
        r = self.build(908, implement)
        r.main()
        note = self.notes["implement"][1]
        self.assertIn("BASE-CHECK-SKIP", note)
        self.assertIn("base での確認は今回できなかった", note)
        self.assertNotIn("確かめ済み", note)                 # 見ていないものを見たと言わない
        self.assertEqual(r.state.get("known_red_gates"), None)
        self.assertFalse(r.base_check_broken)               # 見に行けていないだけで、作業ツリーは壊れていない

    # ---------- 戻せなかった VM に agent を入れない。赤が残っていなくても人間へ回し、理由を残す
    def test_a_working_tree_left_on_base_goes_to_human_with_a_reason_even_when_no_gate_is_red(self):
        self.make_repo(["strings-ok.txt", "feature-ok.txt"])
        (self.vm / "prepared").touch()
        r = self.build(905, lambda n: None)

        def run_code(step):
            self.done.append(step["id"])
            if step["code"] == "gates.sh":
                # 赤は全部 base でも赤くて INFO に落ちた（= ok）が、作業ツリーを base から戻せていない状況
                (pathlib.Path(r.work) / "base-red.txt").write_text("strings\n!restore-failed\n", encoding="utf-8")
                r.note_base_red()
                return True, "INFO strings red (also red on base; not a gate)"
            return True, ""
        r.run_code = run_code
        r.main()
        self.assertEqual(self.steps(r), ["research", "design", "implement", "gates"])
        self.assertEqual(r.state["result"], "human")
        self.assertTrue(r.state["history"][-1]["ok"])           # ゲート自体は赤くない
        self.assertEqual(r.state["history"][-1]["next"], "human")
        # 赤が無くても、なぜ人間へ回したのかと、どこからやり直すのかが記録に残る（kb の note と console がここを読む）
        self.assertIn("作業ブランチへ戻せなかった", r.state["error"])
        self.assertEqual(r.state["resume_step"], "implement")
        self.assertEqual(r.state["known_red_gates"], ["strings"])

    # ---------- 出力の大きいゲートは上限で切る。切っても末尾（普通はそこに失敗の理由がある）を残す
    def test_a_noisy_gate_log_is_truncated_from_the_front_and_says_so(self):
        """VM → runner の転送量と依頼文の肥大を抑えるため 1 ゲート 200KB 上限。上限は失敗にせず切り捨て、
           切った事実を 1 行残す（ADR-0034 と同じ扱い）。切るのは先頭側で、末尾は必ず残す"""
        self.make_repo(["strings-ok.txt", "feature-ok.txt"])
        (self.vm / "prepared").touch()
        def implement(n):
            (self.app / "impl.txt").write_text("実装\n", encoding="utf-8"); self.commit("実装した")
        r = self.build(909, implement)
        (self.ws / "projects" / "basered" / "gates.sh").write_text(NOISY_GATES, encoding="utf-8")
        r.main()
        log = self.gate_log(r, "noisy")
        self.assertIsNotNone(log, "work/gates/noisy.log が無い")
        size = (pathlib.Path(r.work) / "gates" / "noisy.log").stat().st_size
        self.assertLessEqual(size, 204800, f"上限を超えて残している: {size} バイト")
        self.assertTrue(log.startswith("[truncated: kept last "), log[:80])
        self.assertTrue(log.splitlines()[-1].startswith("400 x"), log.splitlines()[-1][:40])   # 末尾は切らない

    # ---------- 退避は HEAD ではなく作業ブランチを押す（base に detached のままでも実装が消えない）
    def test_preserve_pushes_the_working_branch_even_if_head_is_left_on_base(self):
        self.make_repo(["strings-ok.txt", "feature-ok.txt"])
        (self.vm / "prepared").touch()
        r = self.build(906, lambda n: None)
        git(self.app, "fetch", "-q", "origin", "develop")
        git(self.app, "checkout", "-q", "-B", r.branch, "origin/develop")
        (self.app / "impl.txt").write_text("実装\n", encoding="utf-8"); self.commit("実装した")
        head = git_out(self.app, "rev-parse", r.branch).strip()
        git(self.app, "checkout", "-q", "--detach", "origin/develop")   # base を見たまま戻れなかった状態
        wip = run.Run.preserve(r)
        self.assertTrue(wip, "退避ブランチへ push できていない")
        self.assertEqual(git_out(self.origin, "rev-parse", wip).strip(), head)


    # ---------- 配るのは ctl の作業ツリー、正本は origin/<base>（チケット 487）
    def test_a_pj_definition_older_than_the_base_is_reported_as_drift(self):
        """#446 で develop に足した unittest-pull が、main 追従の ctl から配られる版に無いまま run が全緑になっていた。
        行が黙って減るので誰も気づけない。配布版と origin/<base> を比べて 1 行言う（FAIL にはしない）"""
        self.make_repo(["strings-ok.txt", "feature-ok.txt"])
        (self.vm / "prepared").touch()
        def implement(n):
            (self.app / "impl.txt").write_text("実装\n", encoding="utf-8"); self.commit("実装した")
        r = self.build(910, implement, ctl={})
        r.main()
        v = self.verdict(r)
        self.assertTrue(v.splitlines()[0].startswith("INFO pj-drift "), v)
        self.assertIn("origin/develop", v.splitlines()[0])
        self.assertIn("gates.sh (missing: unittest-pull)", v)
        self.assertIn("provision.sh", v.splitlines()[0])
        # 配布経路の問題であって実装役の変更ではないので、FAIL にはしない（implement へ戻さない）
        self.assertNotIn("FAIL", v)
        self.assertEqual(self.steps(r), ["research", "design", "implement", "gates", "review", "sync", "pr"])
        self.assertNotIn("gates->implement", r.state["loops"])
        self.assertIn("PASS feature", v)

    def test_no_drift_line_when_the_distributed_definition_matches_the_base(self):
        self.make_repo(["strings-ok.txt", "feature-ok.txt"])
        (self.vm / "prepared").touch()
        r = self.build(911, lambda n: None, ctl={"branch": "develop"})
        r.main()
        self.assertNotIn("pj-drift", self.gates_txt(r))

    def test_no_drift_line_when_the_pj_definition_belongs_to_another_repo(self):
        """他 PJ の base_branch は別リポジトリのブランチ名。この checkout の origin/<base> と比べると嘘になる"""
        self.make_repo(["strings-ok.txt", "feature-ok.txt"])
        (self.vm / "prepared").touch()
        r = self.build(912, lambda n: None, ctl={"repo": "example/elsewhere"})
        r.main()
        self.assertNotIn("pj-drift", self.gates_txt(r))

    def test_no_drift_line_when_the_pj_definition_is_not_in_git(self):
        """workspace/projects に置いた PJ には git の正本が無い。何も言わない（黙るのが正しい）"""
        self.make_repo(["strings-ok.txt", "feature-ok.txt"])
        (self.vm / "prepared").touch()
        r = self.build(913, lambda n: None)
        r.main()
        self.assertNotIn("pj-drift", self.gates_txt(r))


if __name__ == "__main__":
    unittest.main()
