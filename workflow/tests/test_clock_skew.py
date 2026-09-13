"""貸出直後のゲストの時計が制御系とずれていたら、工程を 1 つも始めずに止まること（チケット 491）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。PATH の先頭に偽の `sandbox` / `scp` / `claude` を置いて runner を回す
（`test_prepare.py` と同じ流儀）。偽 ssh は時計の probe（`date -u +%s`）だけ横取りして
`$FAKE_CLOCK_OFFSET` 秒ずらした epoch を返し、他のコマンドはそのまま bash に流す。

なぜ検知が要るか: プール VM は RAM 込みの snapshot を `qm rollback` で戻すので、復元直後のゲストは
snapshot 取得時刻から時計が再開し、timesyncd の次のポーリング（最大 34 分）まで数日ずれたまま走る。
その間に付いた commit の author date と agent が書く ADR の日付が嘘になる。take 側で合わせる（sandbox
CLI の sync_clock）だけでは NTP が届かない網で黙って通るので、runner 側で必ず測って止める。

- 大きくずれている → 工程 0 件で failed（failure: clock）。claude を起こさず VM は返す
- 許容内 → 通常どおり最初の agent 工程に入り、実測値が state.json に残る
- probe が数字を返さない → 「測れなかった」を黙って通さず同じく failed
- kb run 経由なら、チケットは実行中のまま残らず blocked になり、note に「時計」と書いてある
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
RUNNER = REPO / "workflow" / "bin" / "run"
KB = REPO / "kanban" / "bin" / "kb"
TICKET = "# 調査: 時計ずれの再現\n\n偽の VM で貸出直後の時計を測る。\n"

# VM の /home/dev を $VMROOT に読み替える偽 sandbox。時計の probe だけ横取りする
FAKE_SANDBOX = r"""#!/usr/bin/env bash
echo "$@" >> "$CALLS"
case "$1" in
  take) echo "[clock] guest offset ${FAKE_TAKE_CLOCK:-0}s"; echo "take: sb-t-$2-01 10.77.1.1" ;;
  ssh)  shift 2; cmd="${1//\/home\/dev/$VMROOT}"
        if [[ "$cmd" == "date -u +%s" ]]; then
          if [[ -n "${FAKE_CLOCK_BAD+x}" ]]; then printf '%s\n' "$FAKE_CLOCK_BAD"; exit 0; fi
          echo $(( $(date -u +%s) + ${FAKE_CLOCK_OFFSET:-0} )); exit 0
        fi
        SANDBOX_APP_DIR="$VMROOT/app" HOME="$VMROOT" CLAUDE_CODE_OAUTH_TOKEN_FABLE=fake-token-pool CLAUDE_CODE_OAUTH_TOKEN_OPUS=fake-token-pool CLAUDE_CODE_OAUTH_TOKEN_SONNET=fake-token-pool CLAUDE_CODE_OAUTH_TOKEN_HAIKU=fake-token-pool exec bash -c "$cmd" ;;
esac
exit 0
"""

FAKE_SCP = r"""#!/usr/bin/env bash
src="${@: -2:1}"; dst="${@: -1}"; dst="${dst#*:}"; dst="$VMROOT${dst#/home/dev}"
mkdir -p "$(dirname "$dst")" && cp "$src" "$dst"
"""

FAKE_CLAUDE = r"""#!/usr/bin/env bash
echo started > "$VMROOT/claude-ran"
exit 0
"""

PROJECT = """name: {pj}
repo: example/{pj}
base_branch: develop
app_dir: {app}
gates: gates.sh
"""

SKEW_6_DAYS = 578400   # 実測（#478 b325f15 / #486 549d839 が 6 日 17 時間ずれた）


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True)
    if p.returncode: raise AssertionError(f"git {' '.join(args)} @ {cwd}: {p.stdout}{p.stderr}")
    return p.stdout


class ClockSkewTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.vm = self.ws / "vm"; (self.vm / "work").mkdir(parents=True)
        self.bin = self.ws / "bin"; self.bin.mkdir()
        for name, body in (("sandbox", FAKE_SANDBOX), ("scp", FAKE_SCP), ("claude", FAKE_CLAUDE)):
            f = self.bin / name; f.write_text(body, encoding="utf-8"); f.chmod(0o755)
        self.ticket = self.ws / "ticket.md"; self.ticket.write_text(TICKET, encoding="utf-8")
        self.calls = self.ws / "calls.log"; self.calls.write_text("")
        self.sbstate = self.ws / "sandbox-state.json"
        self.sbstate.write_text(json.dumps({str(t): {"ip": "10.77.1.1"} for t in range(900, 920)}), encoding="utf-8")
        origin = self.ws / "origin.git"
        git(self.ws, "init", "-q", "--bare", "-b", "develop", str(origin))
        seed = self.ws / "seed"; seed.mkdir()
        git(seed, "init", "-q", "-b", "develop")
        git(seed, "config", "user.name", "aifactory test"); git(seed, "config", "user.email", "test@example.invalid")
        (seed / "README.md").write_text("pj\n", encoding="utf-8")
        git(seed, "add", "-A"); git(seed, "commit", "-q", "-m", "初期")
        git(seed, "remote", "add", "origin", str(origin)); git(seed, "push", "-q", "-u", "origin", "develop")
        self.app = self.vm / "app"
        git(self.ws, "clone", "-q", str(origin), str(self.app))
        git(self.app, "config", "user.name", "aifactory test"); git(self.app, "config", "user.email", "test@example.invalid")

    def project(self, pj):
        d = self.ws / "projects" / pj; d.mkdir(parents=True, exist_ok=True)
        (d / "project.yml").write_text(PROJECT.format(pj=pj, app=self.app), encoding="utf-8")
        (d / "gates.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        return d

    def env(self, **extra):
        return dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", AIFACTORY_WORKSPACE=str(self.ws),
                    VMROOT=str(self.vm), CALLS=str(self.calls), SANDBOX_STATE=str(self.sbstate), **extra)

    def run_runner(self, pj, task, **extra):
        import datetime
        name = f"{datetime.date.today().isoformat()}-{pj}-{task}"
        p = subprocess.run([sys.executable, str(RUNNER), pj, task, "research", str(self.ticket)],
                           text=True, capture_output=True, env=self.env(**extra))
        return p, self.ws / "runs" / name

    def state(self, run_dir):
        f = run_dir / "state.json"
        self.assertTrue(f.exists(), f"state.json が無い: {sorted(p.name for p in run_dir.iterdir())}")
        return json.loads(f.read_text(encoding="utf-8"))

    # ---------- a: 数日ずれたゲストは、工程を 1 つも始めずに止まる
    def test_a_skewed_guest_clock_stops_the_run_before_any_agent_starts(self):
        self.project("skew")
        p, run_dir = self.run_runner("skew", "911", FAKE_CLOCK_OFFSET=str(SKEW_6_DAYS))
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        s = self.state(run_dir)
        self.assertEqual(s["result"], "failed")
        self.assertEqual(s["failure"], "clock")
        self.assertEqual(s["history"], [])            # 工程は 1 つも走っていない
        self.assertIsNone(s["current"])
        self.assertEqual(s["next"], "human")
        self.assertIn("ずれ", s["error"])
        self.assertIn(str(SKEW_6_DAYS)[:3], s["error"])   # 何秒ずれていたかが読める
        # 実測値は成否に関わらず残る（後から「どの run がいつどれだけずれたか」を数えられる）
        self.assertAlmostEqual(s["clock_offset_s"], SKEW_6_DAYS, delta=60)
        calls = self.calls.read_text(encoding="utf-8")
        self.assertNotIn("claude", calls)             # 嘘の日付でコミットさせない
        self.assertIn("release 911", calls)           # 貸した VM は返す

    # ---------- b: 許容内なら今までどおり進み、実測値が記録に残る
    def test_a_guest_within_tolerance_runs_normally_and_records_the_offset(self):
        self.project("insync")
        p, run_dir = self.run_runner("insync", "912", FAKE_CLOCK_OFFSET="30")
        s = self.state(run_dir)
        self.assertNotEqual(s.get("failure"), "clock")
        self.assertTrue((self.vm / "claude-ran").exists(), p.stdout[-2000:])
        self.assertIn("clock_offset_s", s)
        self.assertLessEqual(abs(s["clock_offset_s"]), 60, s["clock_offset_s"])

    # ---------- b2: take 側の是正も run の記録に残る（誰がいつ合わせたかを後から読む）
    def test_the_take_side_correction_is_kept_in_the_run_log(self):
        self.project("logged")
        p, run_dir = self.run_runner("logged", "916", FAKE_CLOCK_OFFSET="10", FAKE_TAKE_CLOCK="578400s → 0")
        self.assertIn("[clock] guest offset 578400s → 0s", p.stdout)
        self.assertIn("clock: guest offset", p.stdout)          # runner 自身の実測も残る

    # ---------- c: 測れなかったものを「ずれていない」と読み替えない
    def test_a_probe_that_returns_no_number_is_not_treated_as_in_sync(self):
        self.project("noprobe")
        p, run_dir = self.run_runner("noprobe", "913", FAKE_CLOCK_BAD="")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        s = self.state(run_dir)
        self.assertEqual(s["failure"], "clock")
        self.assertEqual(s["history"], [])
        self.assertNotIn("claude", self.calls.read_text(encoding="utf-8"))

    # ---------- d: 許容は env で変えられる（ずれの許し方は運用の判断）
    def test_the_tolerance_can_be_widened_by_env(self):
        self.project("loose")
        p, run_dir = self.run_runner("loose", "914", FAKE_CLOCK_OFFSET="600", AIFACTORY_CLOCK_TOLERANCE_S="900")
        s = self.state(run_dir)
        self.assertNotEqual(s.get("failure"), "clock")
        self.assertAlmostEqual(s["clock_offset_s"], 600, delta=60)

    # ---------- e: 板の側（kb）でも「時計で止まった」と分かる
    def test_kb_run_blocks_the_ticket_and_says_clock(self):
        self.project("skew")
        env = self.env(FAKE_CLOCK_OFFSET=str(SKEW_6_DAYS))
        new = subprocess.run([sys.executable, str(KB), "new", "skew", "research", "時計ずれの再現",
                              "--body", "-", "--id", "915"], input=TICKET, text=True, capture_output=True, env=env)
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)
        r = subprocess.run([sys.executable, str(KB), "run", "915"], text=True, capture_output=True, env=env)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        show = subprocess.run([sys.executable, str(KB), "show", "915"], text=True, capture_output=True, env=env)
        head = show.stdout.split("-" * 60)[0].splitlines()
        t = {l.split(" ", 1)[0]: l.split(" ", 1)[1].strip() for l in head if l.strip()}
        self.assertEqual(t["status"], "blocked", t)
        self.assertIn("時計", t["note"])
        self.assertIn("sandbox reset", t["note"])        # 次の一手（VM を巻き戻して合わせ直す）
        self.assertNotIn("VM を取得できず", t["note"])   # VM は取れている


if __name__ == "__main__":
    unittest.main()
