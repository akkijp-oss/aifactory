"""kb sync が GitHub の PR の状態を見て review チケットを done / blocked にする（チケット 345）。

  python3 -m unittest discover -s workflow/tests -v

VM も claude も使わない。PATH の先頭に偽の `gh` と `sandbox` を置いて kb / dispatch を呼ぶ
（test_gh_token_from_app.py + test_kb_run_note.py と同じ流儀）。

- MERGED の PR を持つ review チケットは done になり、メモの 1 行目に「PR #n マージ済み <日時>」が入る
- OPEN の PR は何も変えない。CLOSED（未マージ）は blocked にし「マージされずに閉じられた」と書く
- 人が書いたメモの 2 行目以降は残る（ADR-0048）
- gh も sandbox も無い環境では、チケットを変えず rc=0 で終わる（理由は stderr に 1 行）
- GH_TOKEN があれば GitHub App（sandbox gh-app token）は呼ばない
- `kb sync --all-review [--pj P]` が review 全件を回して要約を 1 行出す。`--dry-run` は DB を変えない
- `dispatch` は回し始める前に 1 回 sync を呼び、dispatch.log に 1 行残す
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
KB = REPO / "kanban" / "bin" / "kb"
DISPATCH = REPO / "glue" / "bin" / "dispatch"
TOKEN = "ghs_fake_token_value"
TICKET = "# 機能: PR のマージを板に反映する\n\n偽の gh で PR の状態を出し分ける。\n"

MERGED_AT = "2026-09-09T02:11:44Z"
CLOSED_AT = "2026-09-09T05:00:00Z"

# gh pr view <n> -R <repo> --json … の代わり。PR ごとの JSON は環境変数 PR_<n> で渡す（無ければ本物と同じく rc≠0）
FAKE_GH = r"""#!/usr/bin/env bash
if [ -n "$GH_TOKEN" ]; then st=set; else st=empty; fi
echo "gh $* token=$st" >> "$CALLS"
eval "body=\${PR_$3-}"
if [ -z "$body" ]; then echo "GraphQL: Could not resolve to a PullRequest with the number of $3." >&2; exit 1; fi
echo "$body"
"""

FAKE_SANDBOX = r"""#!/usr/bin/env bash
echo "$@" >> "$CALLS"
case "$1" in
  gh-app)
    if [ "$2" = token ]; then
      if [ -n "$APP_FAILS" ]; then echo "[error] GitHub App が install されていない" >&2; exit 1; fi
      echo "TOKEN_VALUE"
    fi
    ;;
  take) echo "[error] pj=$2 に空きなし" >&2; exit 1 ;;
esac
exit 0
""".replace("TOKEN_VALUE", TOKEN)


def pr_json(state, merged_at="", closed_at=""):
    return json.dumps({"state": state, "mergedAt": merged_at or None, "closedAt": closed_at or None,
                       "mergeCommit": {"oid": "0" * 40} if state == "MERGED" else None})


class KbSyncPrStateTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.ws = pathlib.Path(d.name)
        self.bin = self.ws / "bin"; self.bin.mkdir()
        self.empty = self.ws / "empty-bin"; self.empty.mkdir()      # gh も sandbox も無い PATH（この VM / CI と同じ）
        for name, body in (("gh", FAKE_GH), ("sandbox", FAKE_SANDBOX)):
            f = self.bin / name; f.write_text(body); f.chmod(0o755)
        (self.ws / "sandbox-state.json").write_text("{}")
        self.calls = self.ws / "calls.log"
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        AIFACTORY_WORKSPACE=str(self.ws), SANDBOX_STATE=str(self.ws / "sandbox-state.json"),
                        CALLS=str(self.calls), APP_FAILS="", AIFACTORY_ACTOR="pm",
                        PR_300=pr_json("MERGED", MERGED_AT, MERGED_AT),
                        PR_301=pr_json("OPEN"),
                        PR_302=pr_json("CLOSED", "", CLOSED_AT),
                        PR_400=pr_json("MERGED", MERGED_AT, MERGED_AT))
        self.env.pop("GH_TOKEN", None)          # 開発機に本物があっても再現するように

    def kb(self, *args, env=None, input_text=None):
        return subprocess.run([sys.executable, str(KB), *args], input=input_text, text=True, capture_output=True, env=env or self.env)

    def dispatch(self, *args, env=None):
        return subprocess.run([sys.executable, str(DISPATCH), *args], text=True, capture_output=True, env=env or self.env)

    def ticket(self, tid, pr, pj="kumitate", status="review", note=None):
        args = ["new", pj, "feature", f"PR の状態を見る {tid}", "--body", "-", "--id", str(tid), "--status", status]
        if pr: args += ["--pr", str(pr)]
        if note: args += ["--note", note]
        r = self.kb(*args, input_text=TICKET)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return tid

    def show(self, tid):
        """kb show の項目（note は複数行になりうるので、次の欄が来るまでをまとめる。342）"""
        lines = self.kb("show", str(tid)).stdout.split("-" * 60)[0].splitlines()
        out, key = {}, None
        for l in lines:
            head = l.split(None, 1)
            if len(head) == 2 and not l.startswith(" ") and head[0] in ("id", "pj", "kind", "title", "status", "pr", "run", "note", "file", "created", "updated"):
                key = head[0]; out[key] = head[1].strip()
            elif key == "note":
                out["note"] = out.get("note", "") + "\n" + l
        return {k: v.strip() for k, v in out.items()}

    def calls_text(self):
        return self.calls.read_text(encoding="utf-8") if self.calls.exists() else ""

    def finished_run(self, tid, name, pr):
        """PR まで出して human で終わった run の記録（runner が残す形）を置き、チケットの run 欄をそれに向ける"""
        d = self.ws / "runs" / name; d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(json.dumps(
            {"pj": "kumitate", "task": str(tid), "workflow": "feature", "branch": f"sandbox/{tid}-feature-x", "base": "develop",
             "result": "human", "next": "human", "pr_url": f"https://github.com/akkijp/kumitate/pull/{pr}",
             "started": "2026-09-08T09:00:00+09:00", "finished": "2026-09-08T10:00:00+09:00", "history": []},
            ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.kb("set", str(tid), "--run", name).returncode, 0)
        return name

    # ---------- 1 件ずつ
    def test_a_merged_pr_moves_the_ticket_to_done_with_the_merge_time_in_the_note(self):
        self.ticket(940, 300)
        r = self.kb("sync", "940")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        t = self.show(940)
        self.assertEqual(t["status"], "done")
        self.assertEqual(t["note"], f"[run] PR #300 マージ済み {MERGED_AT}")
        self.assertIn("gh pr view 300 -R akkijp/kumitate", self.calls_text())    # repo は PJ 定義から
        self.assertIn("token=set", self.calls_text())
        for where, text in (("calls.log", self.calls_text()), ("stdout", r.stdout), ("stderr", r.stderr),
                            ("note", t["note"])):
            self.assertNotIn(TOKEN, text, where)

    def test_an_open_pr_leaves_the_ticket_alone(self):
        self.ticket(941, 301, note="PM: 急ぎ")
        before = self.show(941)
        r = self.kb("sync", "941")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        after = self.show(941)
        self.assertEqual(after["status"], "review")
        self.assertEqual(after["note"], "PM: 急ぎ")
        self.assertEqual(after["updated"], before["updated"], "変えないときは DB を触らないこと")

    def test_a_closed_unmerged_pr_blocks_the_ticket(self):
        self.ticket(942, 302)
        r = self.kb("sync", "942")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        t = self.show(942)
        self.assertEqual(t["status"], "blocked")
        self.assertIn("PR #302 がマージされずに閉じられた", t["note"])
        self.assertIn(CLOSED_AT, t["note"])
        self.assertIn("kb reopen 942", t["note"])

    def test_the_note_written_by_a_human_survives_the_pr_sync(self):
        """run 由来の行は 1 行目だけ。人の申し送り（2 行目以降）は消さない（ADR-0048）"""
        self.ticket(943, 300)
        self.assertEqual(self.kb("set", "943", "--note", "[run] PR 待ち https://example.invalid/pull/300\nPM: 先に #12 を入れる").returncode, 0)
        self.assertEqual(self.kb("sync", "943").returncode, 0)
        note = self.show(943)["note"]
        self.assertEqual(note.splitlines()[0], f"[run] PR #300 マージ済み {MERGED_AT}")
        self.assertIn("PM: 先に #12 を入れる", note)
        self.assertNotIn("PR 待ち", note)

    def test_without_gh_or_the_app_the_ticket_is_left_alone_and_sync_still_succeeds(self):
        """gh も sandbox も無い環境（この VM / CI）。黙って飛ばす: 状態は変えず rc=0、理由は stderr に 1 行だけ"""
        self.ticket(944, 300)
        env = dict(self.env, PATH=str(self.empty))
        r = self.kb("sync", "944", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.show(944)["status"], "review")
        self.assertIn("[kb] warn:", r.stderr)
        self.assertNotIn("warn", r.stdout)

    def test_an_existing_gh_token_is_used_without_calling_the_github_app(self):
        self.ticket(945, 300)
        r = self.kb("sync", "945", env=dict(self.env, GH_TOKEN="static"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.show(945)["status"], "done")
        self.assertNotIn("gh-app token", self.calls_text())

    def test_dry_run_reports_the_change_without_writing_it(self):
        self.ticket(946, 300)
        r = self.kb("sync", "946", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        line = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((line["id"], line["pr_state"]), (946, "MERGED"))
        self.assertEqual(line["after"]["status"], "done")
        # 下見の形は apply_result の dry と同じ（console は最後の 1 行だけを読み、run と before/after を見る）
        self.assertEqual(set(line["before"]), {"status", "note", "run", "pr", "updated"})
        self.assertEqual(set(line["after"]), {"status", "note", "pr", "run"})
        self.assertIn("run", line)
        self.assertEqual(self.show(946)["status"], "review", "--dry-run で DB を書いている")

    def test_a_ticket_without_a_run_is_synced_from_the_pr_alone(self):
        """run に紐づかない review チケット（人が板で PR を付けた）でも die しない。run も PR も無ければ従来どおり断る"""
        self.ticket(947, 300)
        self.assertEqual(self.show(947).get("run", ""), "")
        self.assertEqual(self.kb("sync", "947").returncode, 0)
        self.ticket(948, None, status="todo")
        r = self.kb("sync", "948")
        self.assertEqual(r.returncode, 1); self.assertIn("run が無い", r.stderr)


    def test_the_pr_state_overrides_the_note_the_runner_left(self):
        """run 記録（PR 待ち）を先に、GitHub の事実を後に当てる。板は `PR 待ち` ではなく `マージ済み` になる"""
        self.ticket(949, 300)
        self.finished_run(949, "2026-09-08-kumitate-949", 300)
        r = self.kb("sync", "949")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        t = self.show(949)
        self.assertEqual(t["status"], "done")
        self.assertEqual(t["note"], f"[run] PR #300 マージ済み {MERGED_AT}")
        # 下見（console が読む形）は最後の 1 行が最終の状態
        d = self.kb("sync", "949", "--dry-run")
        self.assertEqual(d.returncode, 0, d.stdout + d.stderr)
        self.assertEqual(json.loads(d.stdout.strip().splitlines()[-1])["run"], "2026-09-08-kumitate-949")

    # ---------- --all-review
    def test_all_review_walks_every_review_ticket_and_prints_one_summary(self):
        for tid, pr in ((950, 300), (951, 301), (952, 302)): self.ticket(tid, pr)
        self.ticket(953, 400, pj="aifactory")
        r = self.kb("sync", "--all-review", "--pj", "kumitate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[kb] sync --all-review: 対象 3 件 / done 1 / blocked 1 / 変更なし 1 / 飛ばした 0", r.stdout)
        self.assertEqual([self.show(t)["status"] for t in (950, 951, 952)], ["done", "review", "blocked"])
        self.assertEqual(self.show(953)["status"], "review", "--pj で絞った外のチケットを触っている")
        # 絞らなければ残りの PJ も回る（PJ ごとにトークンを取り直す。App のトークンは repo 限定）
        r = self.kb("sync", "--all-review")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("対象 2 件 / done 1 / blocked 0 / 変更なし 1 / 飛ばした 0", r.stdout)
        self.assertEqual(self.show(953)["status"], "done")
        self.assertIn("gh-app token aifactory", self.calls_text())

    def test_all_review_is_quiet_and_successful_when_gh_is_missing(self):
        for tid, pr in ((954, 300), (955, 302)): self.ticket(tid, pr)
        r = self.kb("sync", "--all-review", env=dict(self.env, PATH=str(self.empty)))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("対象 2 件 / done 0 / blocked 0 / 変更なし 0 / 飛ばした 2", r.stdout)
        self.assertEqual(len([l for l in r.stderr.splitlines() if "warn" in l]), 1, "PJ ごとに 1 行だけ")
        self.assertEqual([self.show(t)["status"] for t in (954, 955)], ["review", "review"])

    def test_all_review_refuses_an_id_and_a_run(self):
        r = self.kb("sync", "940", "--all-review")
        self.assertEqual(r.returncode, 1); self.assertIn("一緒に使えない", r.stderr)
        r = self.kb("sync", "--all-review", "--run", "2026-09-06-kumitate-940")
        self.assertEqual(r.returncode, 1); self.assertIn("一緒に使えない", r.stderr)
        r = self.kb("sync")
        self.assertEqual(r.returncode, 1); self.assertIn("--all-review", r.stderr)

    def test_all_review_dry_run_does_not_write(self):
        self.ticket(956, 300)
        r = self.kb("sync", "--all-review", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("対象 1 件 / done 1", r.stdout)
        self.assertEqual(self.show(956)["status"], "review")

    # ---------- dispatch
    def test_dispatch_syncs_the_review_tickets_once_before_it_starts(self):
        """todo が 1 件も無くても、配車は先に sync を回してログに 1 行残す（レビュー待ちが自動で片付く）"""
        self.ticket(957, 300)
        r = self.dispatch()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.show(957)["status"], "done")
        log = (self.ws / "logs" / "dispatch.log").read_text(encoding="utf-8")
        sync_lines = [l for l in log.splitlines() if "\tsync " in l]
        self.assertEqual(len(sync_lines), 1, log)
        self.assertIn("sync --all-review: 対象 1 件 / done 1", sync_lines[0])
        self.assertNotIn("take", self.calls_text(), "todo が無いのに VM を取りに行っている")

    def test_dispatch_dry_run_and_resume_paused_do_not_sync(self):
        """--dry-run は状態を進めない約束。--resume-paused は 5 分ごとの timer なので GitHub を叩かない"""
        self.ticket(958, 300)
        for args in (("--dry-run",), ("--resume-paused",)):
            r = self.dispatch(*args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(self.show(958)["status"], "review", f"dispatch {args} が sync している")
        self.assertNotIn("gh pr view", self.calls_text())


if __name__ == "__main__":
    unittest.main()
