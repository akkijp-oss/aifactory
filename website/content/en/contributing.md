# Contributing and rules for AI sessions

This repository is published under Apache-2.0 ([akkijp/aifactory](https://github.com/akkijp/aifactory)). Contributions come in as GitHub pull requests. It is written on the assumption that humans (the maintainer and contributors) and several AI sessions (Claude Code) take turns, and sometimes work at the same time; humans and AIs enter the work the same way. `CONTRIBUTING.md` (how to contribute) and `SECURITY.md` (where to report vulnerabilities) at the repository root are the sources of truth; this page is a rendering of them.

## Find your position

1. Grasp the big picture (4 sections) from the root `README.md`
2. Read the design and contract in the `README.md` of the section you are about to work on
3. If you touch the sandbox, run the real-machine check commands in `sandbox/STATUS.md` and confirm for yourself that the recorded progress matches reality. If not, fix `STATUS.md` to match reality first
4. If you change a decision, add one file to `docs/adr/` (never rewrite an existing ADR)

## Rules

- **Reality wins.** When documents and the real machine disagree, trust the machine and fix the documents
- Places that need a human (Tailscale approval, `claude setup-token`, and so on) are marked 🧑 in `BUILD.md`. Stop there and ask
- Never write secrets (tokens, keys) into this repository. They live in `~/.config/sandbox/` (the "secrets" section of `sandbox/README.md`)
- Facts about a personal environment (host names, LAN addresses, the list of target repositories) do not go into the repository either. Put them in the workspace (`$AIFACTORY_WORKSPACE/docs/`). Documentation examples use the bundled `examples/projects/kumitate/` or the generic `<pj>` / `myapp`
- **Assume another AI session is running at the same time**
    - Re-read `ls docs/adr/` for the highest number before adding an ADR
    - Do not revert changes in `git status` that you did not make. Commit only your own
    - Do not restart, roll back or rebuild lent sandbox VMs. Any job touching every VM reads the lending state right before and skips them
    - Update ticket state through `kanban/bin/kb`. When calling the runner directly, catch up with `kb sync`
- The priority is **quality over cost** (the maintainer's decision, 2026-09-06). Prefer builds that work reliably, do not break, and can be read later over clever savings

## Where to write what

| Kind | Location |
|---|---|
| Current position, open questions, history | `docs/ledger.md` |
| Each section's design and contract | `<section>/README.md` |
| Build steps (commands and completion criteria) | `sandbox/BUILD.md` |
| Progress table and real-machine checks | `sandbox/STATUS.md` |
| Operations (lend, return, failures) | `sandbox/OPERATIONS.md` |
| Reasons for design decisions | `docs/adr/NNNN-*.md` |
| Traps encountered | "traps and handling" in `workflow/README.md` |
| User-facing change history | `CHANGELOG.md` |
| Notes on your own environment | `$AIFACTORY_WORKSPACE/docs/` (outside the repository) |
| Readable renderings for people | `docs/*.html`, this site (`website/`) |

## How to make changes

| Changing | Before | After |
|---|---|---|
| workflow yml / roles / project.yml | Check the schema and prompts with `kb run <id> --dry-run` | Takes effect from the next run. Leave running runs alone |
| `sandbox/bin/sandbox` | `bash -n` | Update the copy on PATH with `sandbox/bin/install.sh` |
| The runner (`workflow/bin/run`) | Confirm no run is in progress (Python is safe to edit while running, but behaviour would mix) | Check with `python3 -m unittest discover -s workflow/tests` and `--dry-run` |
| console / mcp | `python3 -m unittest discover -s console/tests` | If it runs under launchd, `launchctl kickstart -k gui/$(id -u)/com.aifactory.console` |
| Proxmox scripts | Confirm no VM is lent with `sandbox ls` | Update `STATUS.md` |
| kanban / glue | Test with `AIFACTORY_WORKSPACE=<another directory>` (or `KB_ROOT`) | |
| This site | Edit `website/content/ja/` and translate into `en/` | `mkdocs build --strict` must pass |

CI (`.github/workflows/ci.yml`) runs the unittests in `console/tests` and `workflow/tests` and `mkdocs build --strict` on every pull request. Run the same things locally before opening a PR.

## Commits and pull requests

- Select only your own changes with `git add`. Never `git add -A`
- "What and why" on line 1 of the message, with decisions and measurements in the body
- Run records (`workspace/runs/`) and the ledger (`workspace/kanban/kanban.db`) live in the workspace and are **not tracked** by the repository. Do not include them in a PR
- Do not push to main directly: branch and open a pull request. Merging is a human decision (agents do not push)
- A PR that changes the design comes with one ADR. A user-visible change gets one line in `CHANGELOG.md`

## Extra notes for AI sessions

- Do not trust the state you saw at the start of the conversation. Re-read `git status` / `sandbox ls` / `ls docs/adr/` right before acting
- Do not fill gaps with guesses. Verify against the real machine and the files
- Stop at places that need a human (🧑) and say in one line what you need
- Before finishing, write what you did and what remains into `STATUS.md` or `ledger.md`
- Never print secrets in logs or reports. Show tokens masked
