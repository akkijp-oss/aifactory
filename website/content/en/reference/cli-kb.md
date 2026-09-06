# kb (kanban CLI)

`kanban/bin/kb`. The ticket ledger (SQLite) holding ids, state and history, and the entry point for calling the runner. Python 3 standard library only.

```
kb new <pj> <kind> <title> [--body FILE|-] [--pr N] [--id N] [--status S] [--note TEXT] [--attach FILE]...
kb list [--status S] [--pj P] [--all]
kb show <id>
kb start|review|done|reopen <id> [--note TEXT]
kb block <id> --note TEXT
kb set <id> [--status S] [--pr N] [--run DIR] [--note TEXT] [--kind K]
kb append <id> [--section S] [--text T]
kb attach <id> <file>...
kb attachments <id> [--json]
kb detach <id> <name>
kb next [--pj P] [--json]
kb run <id> [--workflow W] [--dry-run] [--keep] [--resume] [--from [STEP]] [--branch B] [--force] [--wait [minutes]]
kb sync <id> [--run DIR]
kb sync --all-review [--pj P]
kb run-note <run> [--result done|abandoned] [--pr N] [--text T] [--force]
kb history <id>
kb render
```

## Locations

| Item | Path |
|---|---|
| Database (source of truth) | `$AIFACTORY_WORKSPACE/kanban/kanban.db` (default `<repo>/workspace/kanban/`, not tracked by git) |
| Bodies (source of truth) | `kanban/tickets/<id>-<pj>-<slug>.md` under the same root |
| Attachments (source of truth) | `kanban/attachments/<id>/<name>` under the same root (ADR-0041) |
| Board (generated) | `kanban/BOARD.md` under the same root |
| Relocating | Environment variable `AIFACTORY_WORKSPACE=<dir>` moves the whole workspace; `KB_ROOT=<dir>` moves only the database, tickets and BOARD (for tests) |

## States

| State | Label | Meaning |
|---|---|---|
| `todo` | not started | Filed |
| `in_progress` | running | The runner is executing |
| `review` | awaiting review | A PR exists. Waiting for a human |
| `blocked` | waiting for a human | Went to human, crashed, or no project.yml. The note says why |
| `done` | done | Merged, or finished without a PR |

## Commands

### new

```bash
kb new <pj> <kind> "<title>" [--body FILE|-] [--pr N] [--id N] [--status S] [--note TEXT]
```

| Argument | Meaning |
|---|---|
| `pj` | A project definition directory must exist (`$AIFACTORY_WORKSPACE/projects/<pj>/`, else `examples/projects/<pj>/`) |
| `kind` | Must match `workflow/kit/workflows/<kind>.yml` (chore / bug / feature / hotfix / research / merge-pr) |
| `title` | Line 1. Truncated at 70 characters |
| `--body` | Body file. `-` for standard input. No body if omitted |
| `--pr` | Target PR number for merge-pr. Written as `pr: N` on line 2 of the body |
| `--id` | Explicit number (default `MAX(id)+1`, minimum 100). Error if it exists |
| `--status` | Initial state (default todo) |
| `--note` | Note |

Output: one line `<id> <state> <pj> <kind> <PR> <title>` plus the body path. The file slug comes from the ASCII part of the title, or the kind if there is none.

### list / show / next

```bash
kb list                          # everything except done
kb list --status review          # by state
kb list --pj kumitate --all      # by project; --all includes done
kb show 204                      # all fields + body
kb next                          # the oldest todo
kb next --pj kumitate --json     # JSON (for dispatch and external tools; path holds the body's absolute path)
kb resumable [--pj P] [--json]   # tickets paused by the Claude usage limit, and whether their reset time has passed (read by dispatch --resume-paused; ADR-0043)
```

### Advancing state

```bash
kb start 204                     # → in_progress
kb review 204                    # → review
kb done 204 --note "merged"      # → done
kb block 204 --note "waiting for a production-impact decision"   # → blocked (--note required)
kb reopen 204                    # → todo
kb set 204 --status review --pr 300 --run 2026-09-06-kumitate-204 --note "…" --kind feature
```

Changing `--pr` also rewrites the `pr:` line in the body (the runner reads it from there). `--run` is the run directory name (relative to `workspace/runs/`). `--kind` is validated. Everything is recorded in the history and BOARD.md is regenerated.

`kb set 204 --note ''` clears the note (NULL in the DB). A field you do not pass is left alone. Over MCP and the HTTP API (`console`), `note` is treated as "present as an empty string = clear it, key absent = leave it alone"; an empty string used to be ignored as "not given". `status` / `kind` / `pr` still ignore an empty string as "not given".

### append

```bash
kb append 204 --section "PM 補足" --text "the `._*` files in 218 are AppleDouble"
kb append 204 --section "PM 補足" < memo.md      # without --text, read from stdin
```

Appends to the **end** of the ticket body. With `--section`, a `## <heading>` line is written first. Empty text, or a ticket whose body file is missing, is an error (exit code 1).

The insertion point is fixed at the end. It does not go before `## 完了条件` because `kb` has no way to recognise sections, and appending at the end keeps the change in one place. The heading is what tells you a passage was added later.

The text itself lives in the body (the file is the source of truth). The history only records when and how much was added, as `body  - → append 24字 (PM 補足)` — `history` has three columns (`field` / `old` / `new`) and does not keep diffs.

### attach / attachments / detach

```bash
kb attach 204 ~/Desktop/screen.png spec.pdf    # copied (the original stays where it is)
kb attachments 204                             # name, size, type, added
kb attachments 204 --json                      # [{"name","size","type","added"}]
kb detach 204 screen.png
kb new kumitate bug "Bug: saving does nothing" --body - --attach screen.png   # attach while filing
```

Attach images (screenshots, design mockups) or files (spec PDFs, CSVs, config files) to a ticket. They are copied into `$AIFACTORY_WORKSPACE/kanban/attachments/<id>/` and **nothing is written into the ticket body**. The files themselves are the source of truth; the listing is derived from them by `kb show`, the console ticket page and MCP `ticket_show` (ADR-0041).

- Names are sanitized (path separators, `..` and control characters are removed, Markdown syntax (`` ` `` `*` `[` `]` `<` `>` `|`) becomes `_`, runs of whitespace collapse to one, and the name is cut to 120 bytes). The name is embedded verbatim in each step's prompt, which is why it is kept short and plain. A name that already exists gets `-2`, `-3` … before the extension instead of overwriting
- Limits are **20 MiB per file and 100 MiB per ticket**. Exceeding either is an error (exit code 1). With several files, it stops at the first failure and keeps what already went in
- Adding and removing are recorded in the history as `attachment  - → add screen.png (12.3 KiB)` / `attachment  screen.png → removed`
- For a ticket with no attachments, the output of `kb show` is unchanged
- `kb new --attach` can **create the ticket and still fail to attach** (over the size limit, for instance). The id is printed on standard output but the exit code is not 0. The ticket exists, so retry just the attachment with `kb attach <id> <file>`

On `kb run`, the runner places the attachments in `~/work/<id>/attachments/` on the VM and adds one line to every step prompt:

```
- 添付: /home/dev/work/204/attachments/（screen.png, spec.pdf。画像・PDF は Read で開いて見ること。本文と食い違うときは添付を優先し、その旨を報告に書く）
```

The agent (Claude Code) can open images (PNG, JPG …) and PDFs with the Read tool, so "this part of this screen" and "exactly like this table" can be handed over as the real thing. With no attachments, nothing is added to the prompt.

!!! warning "Do not attach secrets"
    `attachments/` lives in the workspace, which is not tracked by git, so it is **not** scanned by `bin/oss-check.sh` for secrets (that check only verifies the location is untracked). Do not attach tokens, keys or real `.env` values.

!!! note "Proxmox backend only for now"
    Only the Proxmox backend copies attachments to the VM. Pull backends (macOS, Windows and Linux workers) use a different transfer path and are not covered yet; their prompts get no attachment line either.

### run

```bash
kb run 204 [--workflow W] [--dry-run] [--keep] [--resume] [--from [STEP]] [--branch B] [--force] [--wait [minutes]]
```

1. Error if the project has no `project.yml`. `done` tickets error except with `--dry-run` (`reopen` first)
2. Sets `in_progress` and records the run directory name (`<date>-<pj>-<id>`; the directory lives under `workspace/runs/`)
3. Calls `workflow/bin/run <pj> <id> <workflow> <body path> [flags]`. `--workflow` only changes how this run is executed; `kind` is left alone (the history records `workflow → <name>` and `runs/<run>/state.json` holds the authoritative value; ADR-0030). A `--resume` without `--workflow` restarts with the `workflow` from that `state.json`
4. Afterwards reads `state.json` and advances the state (table below)

With `--wait`, a run whose project pool is full does not fail: it waits for a free VM and then starts (minutes; 60 when the value is omitted). While waiting the ticket stays `in_progress`, and the console board and run page show "waiting for a free VM" with the elapsed time. Only when the limit is exceeded does the ticket go back to `todo`, with the reason in its note (ADR-0031).

With `--from`, a run that ended at `human` is redone **on a new VM**, continuing from the recorded wip branch and starting at the given step. Omit the step and it starts at the step recorded as the one to redo. The name of the previous run is passed to the runner in an environment variable, so its artifacts come along to the new VM (ADR-0036). The previous review findings are put in the prompt only when the previous `review.md` was a FAIL (ADR-0053). It cannot be combined with `--resume`.

!!! warning "Resuming the same ticket twice at once is refused"

    The wip branch name is derived from the ticket and the workflow, so resuming the same ticket twice with `--from` lets whichever run finishes last overwrite the other's work. While the ledger says the ticket is in progress and that run's record has not finished either (no `finished` in `state.json`), `kb run --from` stops with an error. If the run is actually done and only the ledger is stale, bring it up to date with `kb sync <id>`. Add `--force` only when you mean to go ahead anyway (ADR-0053).

| state.json | State | Note |
|---|---|---|
| `pr_url` contains `MERGED` | done | Merged URL |
| `pr_url` present | review | PR URL |
| `result: end`, no PR | done | Finished without a PR (research etc.) |
| `result: human`, no PR | blocked | Handed to a human (wip branch) |
| `result: human`, `failure: quota` (Claude usage limit) | **todo** | paused; after `retry_after` (the reset time) `dispatch --resume-paused` continues it with `kb run --from`. Once `quota_hits` reaches `AIFACTORY_RESUME_MAX_HITS` (default 6) it becomes blocked |
| `result: human`, `failure: key` (token invalid, expired or out of credit) | blocked | fix the token, then `kb run --from` (the note holds the command) |
| `result: failed` | blocked | Could not take a VM, so no step ran (the last line of `error` goes into the note) |
| `result: failed` with `failure: wait_timeout` | todo | `--wait` ran out before a VM came free. There is nothing to fix, so the ticket goes back to todo |
| No `finished`, runner exited non-zero | blocked | Runner exited without a record, rc=N |

`--dry-run` leaves the state unchanged. The exit code is the runner's (0 = end or PR present, 2 = human).

When the same ticket is run again (today's run directory already exists, or `--from` / `--branch` was given), the run line of the note is replaced with `[run] 再走中（attempt N・workflow W）`. Without that, the "handed to a human (wip: …)" note from the previous stop stays on as the note of a running ticket and the list looks out of date. The previous run line remains in `kb history`. A first run leaves the note alone.

The "Note" column above does not replace the whole note: only **the first line, the one starting with `[run] `**, is rewritten (`kb run`, `kb sync` and reruns all follow the same rule; ADR-0048). Everything from the second line on belongs to people and a run never removes it. When a person writes with `kb set --note`, `kb block --note` and so on, they still write the whole note (`--note ''` empties it), and the next run rewrites its own first line.

```
[run] PR 待ち https://github.com/akkijp/kumitate/pull/300   <- the machine rewrites this one line
Mac (Claude Code MBP) で実施。Linux sandbox は gates 赤のため   <- written by a person (never removed)
```

The note is a **summary of the state**. Keep longer hand-offs in the ticket body under `## PM 補足` (`kb append --section "PM 補足"`). On existing tickets whose first line is a run-produced sentence from before `[run] ` existed (`人間へ…`, `PR 待ち…` and the like), that line is dropped by the next `kb run` / `kb sync`. No data migration is needed.

### run-note

```bash
kb run-note 2026-09-06-kumitate-204 --result done --pr 300 --text "wip から PR を作ってマージした"
```

Records that a human closed the run out — opened a PR from the wip branch and merged it, or gave up on it. It only adds `human: {at, by, result, pr_url, text}` to `runs/<run>/state.json`; the `result` the runner settled on is left alone (ADR-0039).

| Option | Meaning |
|---|---|
| `--result` | `done` (a human finished it; the default) or `abandoned` (dropped) |
| `--pr` | The number of the PR the human merged. It becomes a URL when the project defines `repo`, otherwise `#N` |
| `--text` | What was done. Shown on the run page of the console |
| `--force` | Add to a run that already has a human record (omitted fields keep their previous values) |
| `by` | `AIFACTORY_ACTOR` if set, otherwise `USER` |

Runs without `finished` (the runner is still writing) and runs that already have a `human` record (without `--force`) are refused. The console then leads the run's "outcome" panel with "人間が PR #n で仕上げました（完了）。" and stops offering the command to continue from where it stopped.

`kb set <id> --pr N` and `kb done <id>` transcribe the same thing automatically when the ticket's run is still waiting on a human and has no human record yet (the console ticket page and the MCP `ticket_action` go through the same path). PRs the runner opened itself are not transcribed (`kb sync` does not call it). If transcription fails, the ticket is still updated and the reason is printed as `[kb] warn:`.

### sync

```bash
kb sync 204 [--run DIR]
kb sync --all-review [--pj P] [--dry-run]
```

Re-reads `state.json` and aligns the state. Use it after calling the runner directly, when `kb run` died midway, or to import a run done by another session. If `finished` is missing, the state stays `in_progress` and the note records the next step.

On top of that, when the ticket is in `review` and carries a PR number, `kb sync` asks GitHub **what happened to that PR** with `gh pr view` (ADR-0050). Tickets with no run at all (someone added the PR from the board) can be synced too, as long as they are in `review` and have a PR. Only a ticket with neither a run nor a PR is refused.

| PR state | Ticket | First line of the note |
|---|---|---|
| `MERGED` | `done` | `[run] PR #n マージ済み <mergedAt>` |
| `CLOSED` (not merged) | `blocked` | `[run] PR #n がマージされずに閉じられた <closedAt>。作り直すなら kb reopen <id> → kb run <id>` |
| `OPEN` / unknown / lookup failed | unchanged | not touched |

Only two fields decide this: `state` and `mergedAt`. Marking a ticket `done` by mistake is the worst outcome, so anything unclear means "do nothing". The timestamp is written exactly as GitHub returned it (with the trailing `Z`).

```bash
kb sync --all-review              # every ticket in review, across all projects
kb sync --all-review --pj asura   # narrow to one project
kb sync --all-review --dry-run    # print one JSON line per ticket that would change, without writing
```

`--all-review` walks the `review` tickets in id order and looks only at the PR state (it does not re-evaluate runs). It prints one summary line at the end.

```
[kb] sync --all-review: 対象 5 件 / done 2 / blocked 1 / 変更なし 2 / 飛ばした 0
```

`gh` authenticates with the same per-project GitHub App used for opening PRs (ADR-0008 / ADR-0030), and a token is minted per project because App tokens are repository-scoped. An existing `GH_TOKEN` in the environment is used as is. **Where neither the App nor `GH_TOKEN` is available (a dev machine, CI), the check is skipped silently**: tickets are left alone, the exit code stays 0, and the reason is printed as a single `[kb] warn:` line on stderr (one line per project with `--all-review`).

Updates that come from a PR are still not transcribed into the run record (ADR-0039) — the "`kb sync` does not transcribe" rule is unchanged.

### history / render

```bash
kb history 204                   # time / field / old → new
kb render                        # regenerate BOARD.md (normally automatic)
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Argument or existence error (`[kb] error: …` on stderr) |
| The runner's code | `kb run` returns the runner's exit code as is |

## Implementation notes

- Tables: `tickets` (id, pj, kind, title, status, file, pr, run, note, created, updated) and `history` (ticket, at, field, old, new)
- Single-Mac assumption. Writing the same database from several machines will conflict
- Makes no decisions. Calls no LLM
