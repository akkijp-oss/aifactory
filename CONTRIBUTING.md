# Contributing

Thanks for looking. aifactory is written to be worked on by humans and AI sessions alike, often at the same time. The same rules apply to both. Japanese version of this page: `website/content/ja/contributing.md` (rendered on the documentation site).

## Before you start

1. Read `README.md` for the four areas, then the `README.md` of the area you are touching (it holds the contract and the lessons learned)
2. `git status`, `ls docs/adr/`: another session may be working in the same checkout. Never revert changes you did not make; commit only your own files (`git add <paths>`, not `git add -A`)
3. Never restart, roll back or rebuild a sandbox VM that is lent out (`sandbox ls`)
4. Run `bin/install-hooks.sh` once per clone. It installs a pre-commit hook (staged changes: secrets, private hostnames, forbidden paths) and a pre-push hook (gitleaks on the pushed range plus `bin/oss-check.sh`). Put the `gitleaks` binary on your PATH

## Making a change

| Changing | Before | After |
|---|---|---|
| workflow YAML / roles / `project.yml` | `kb run <id> --dry-run` (schema check, prompt preview) | it applies to the next run; running runs are not touched |
| `sandbox/bin/sandbox`, Proxmox scripts | `bash -n`; `sandbox ls` to confirm nothing is lent out | `sandbox/bin/install.sh` |
| runner (`workflow/bin/run`) | confirm no run is in progress | `--dry-run` |
| `kb` / glue / console | tests with a temporary `AIFACTORY_WORKSPACE` | |
| documentation site | edit `website/content/ja/`, mirror to `en/` | `mkdocs build --strict` |

- Tests: `python3 -m unittest discover -s console/tests` and `python3 -m unittest discover -s workflow/tests`. They need `pyyaml` and `jsonschema`, nothing else (no VM, no Claude)
- A new design decision gets a new ADR under `docs/adr/` (`NNNN-slug.md`, sections 状況 / 決定 / 理由 / 結果 / 状態). Existing ADRs are never edited; supersede them
- Keep secrets, hostnames, LAN addresses and private project data out of the repository. Operational data belongs in `workspace/` (git-ignored) or wherever `AIFACTORY_WORKSPACE` points
- Commit messages: first line says what and why. Japanese or English are both fine

## Pull requests

- Open a PR against `main`. CI runs the tests, `bash -n` and the strict docs build
- Fill in the PR template. If behaviour changed, update the area README and the site (ja + en)
- Small, reviewable PRs. One design change per PR

## For AI sessions

- Do not trust what you saw at the start of the conversation; re-read `git status`, `sandbox ls` and `ls docs/adr/` right before acting
- Do not fill gaps with guesses; check the machine and the files
- Stop at steps marked 🧑 (human needed) and say in one line what you need
- Never print tokens; mask them

## License

By contributing you agree that your contributions are licensed under the Apache License 2.0 (see `LICENSE`).
