# FAQ

## General

??? question "What is this for?"
    It runs development work on the several repositories you are involved in unattended, apart from two points: writing the request and reviewing the PR. It implements the talk's (Dan Isler, "FORGET Loop Engineering. Agentic Engineering is about THIS") idea that the engineer appears only for the planning at the start and the review at the end, leaving the middle to agents and code.

??? question "How is it different from lining up agents in git worktrees?"
    Worktrees plus a terminal multiplexer line up several agents on the Mac; they share databases and ports, so isolation is weak and reproducing the environment was manual. This factory gives each agent its own VM and rolls it back to a clean state afterwards. It is the next step the talk describes: "worktrees are a good start but not the destination".

??? question "Where does the LLM run? Inside the workflow?"
    Not in the workflow. It runs inside the VM as `claude -p`. The workflow is a definition of "which step calls which role with which model", and the runner (Python on the Mac) reads it and starts the process over ssh. One more place: intake calls an LLM once on the Mac. Details in [Where the LLM runs](concepts/where-llm-runs.md).

??? question "What does it cost?"
    Per run, agents take a few minutes (a mix of Fable / Opus / Sonnet); gates take minutes to an hour but consume no tokens. The priority is "quality over cost" (the maintainer's decision, 2026-09-06): judgement goes to Fable and implementation to Opus. To make it cheaper, edit `routes.env`.

## Usage

??? question "Any tips for writing a free-text request?"
    Name the project ("in kumitate") and say what happens and what you expect. Stating the scope ("seeds only, do not change the models") and whether production is affected keeps the agent inside the scope and stops the planner from writing STOP. If you already know the project or kind, write `pj:` / `kind:` lines at the top and the LLM will not guess.

??? question "intake decided wrongly. Now what?"
    Fix it with `kb set <id> --kind bug` or `--pr N`. If it keeps making the same mistake, adjust the decision guidance inside `glue/bin/intake`.

??? question "Can merging be automated after the PR?"
    Run a `merge-pr` ticket (`kb new <pj> merge-pr "…" --pr N`) and it resolves conflicts → gates → review → merge unattended. Whether to automate the merge itself is a human decision.

??? question "I want to redo a ticket"
    `kb reopen <id>` → `kb run <id>`. A same-day rerun moves the previous `runs/` directory to `-attemptN`.

??? question "I want to stop midway"
    Ctrl-C the runner process. The VM stays lent, so return it with `sandbox release <id>` or continue with `kb run <id> --resume`.

??? question "I want to look inside the VM"
    `sandbox ssh <id>` logs in. The app opens at the URL from `sandbox url <id>`. The VM rolls back when the run ends, so run with `kb run --keep` if you want to look afterwards.

## Configuration

??? question "I want the agent to know a project-specific caveat every time"
    `facts` in `$AIFACTORY_WORKSPACE/projects/<pj>/project.yml`. Prohibitions go in `forbidden`, reviewer checks in `review_points`. Rules for every project go in `workflow/kit/roles/_common.md`. Details in [Configuration](concepts/configuration.md).

??? question "I want to change the model"
    Globally: `workflow/kit/routes.env`. One step: `model_class` in the workflow yml. One run: the environment variable `CLAUDE_MODEL`.

??? question "A gate is already red on base"
    Add the gate name to `known_red_gates` in `project.yml`. The runner downgrades its FAIL to INFO and does not send the agent back to fix it. Remove it once the fixing PR is merged.

??? question "I want to add a repository"
    [Add a project](guides/add-project.md): copy the bundled `examples/projects/kumitate/` to `$AIFACTORY_WORKSPACE/projects/<pj>/`, then do the six things: provision.sh, template and pool, project.yml, gates.sh, token, and installing the GitHub App.

## Safety

??? question "Could an agent push bad code?"
    Agents do not push (a shared rule). Pushing and PRs are done by code (the runner), and a human reviews the PR. Red gates (lint / test) send the work back before the PR, and the reviewer looks for changes outside the scope and weakened tests.

??? question "Can a VM reach the internal network?"
    No. Connections from a VM reach only the internet and the DNS on sb-gw; the LAN, the Proxmox host, other VMs and the tailnet are dropped by the firewall (ADR-0010).

??? question "Where are the tokens? Can they leak?"
    Only in `~/.config/sandbox/` on the Mac, never in the repository. They are injected into the VM's tmpfs on every take and vanish on rollback. GitHub tokens are scoped to one repository and expire after an hour. Details in [Security and secrets](concepts/security.md).

## Operations

??? question "Can I work alongside another session?"
    Yes, with rules: re-read ADR numbers, do not revert other people's changes, do not touch lent VMs, update state through `kb`. See [Working with multiple sessions](guides/multi-session.md).

??? question "What if the Proxmox host goes down?"
    Power it back on (whether WoL / IPMI is available depends on your setup; otherwise it is the physical button on site). The pool has onboot=0, so start VMs by hand with `qm start`.

??? question "How is this site updated?"
    Edit `website/content/ja/`, translate into `website/content/en/`, confirm `mkdocs build --strict` passes, and open a pull request. The sources of truth are the README / ADR files in the repository, so fix those first.

??? question "Where do I write facts about my own environment (host names, LAN, token expiry)?"
    In the workspace (`$AIFACTORY_WORKSPACE/docs/`). The repository is public, so facts about a personal environment do not go in it.
