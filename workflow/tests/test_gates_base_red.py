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
  ssh) shift 2; SANDBOX_APP_DIR="$APP" exec bash -c "$1" ;;
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
  if "$@" > "$HOME/gates/$name.log" 2>&1; then echo "PASS $name"; else echo "FAIL $name (~/gates/$name.log)"; rc=1; fi
}
check() { if [ -f "$1" ]; then echo "ok $1"; else echo "missing $1"; return 1; fi; }
gate strings check strings-ok.txt
gate feature check feature-ok.txt
gate env-ready check "$HOME/prepared"
exit $rc
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
    def build(self, task, implement, prepare=False):
        self.make_project(prepare=prepare)
        r = run.Run("basered", str(task), "feature", str(self.ticket))
        self.assertEqual(r.base, "develop")
        work = self.ws / f"work-{task}"; work.mkdir()
        r.work = str(work)
        self.done = []; self.gates_n = 0
        app = self.app

        def sb(cmd, input_text=None, check=True):
            p = subprocess.run(["bash", "-c", cmd], text=True, capture_output=True, input=input_text,
                               env={**os.environ, "SANDBOX_APP_DIR": str(app)})
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
        self.assertIn("--- strings.log on base (tail 30)", txt)
        self.assertIn("missing strings-ok.txt", txt)
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
        # 未コミットの作業が stash → pop で戻っている
        self.assertEqual((self.app / "README.md").read_text(encoding="utf-8"), "書きかけ\n")
        self.assertEqual((self.app / "scratch.txt").read_text(encoding="utf-8"), "メモ\n")
        self.assertEqual(git_out(self.app, "stash", "list").strip(), "")
        self.assertEqual(git_out(self.app, "rev-parse", "--abbrev-ref", "HEAD").strip(), r.branch)

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


if __name__ == "__main__":
    unittest.main()
