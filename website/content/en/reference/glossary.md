# Glossary

| Term | Meaning |
|---|---|
| **ADR** | Architecture Decision Record. `docs/adr/NNNN-*.md`. One decision per file, append only |
| **agent step** | A workflow step performed by a role. `claude -p` runs inside the VM |
| **artifact** | A step's input or output file. Placed in `~/work/<id>/` in the VM, collected into `runs/…/work/` on release |
| **base** (branch) | The source of the work branch and the PR target. `base_branch` in `project.yml` (`hotfix_base` for hotfix) |
| **base** (template) | The VM template shared by every project, `sb-base` (9100) |
| **blocked** | Ticket state: waiting for a human. The note says why |
| **clean** | The snapshot name of pool VMs. The baseline before lending (app running, firewall included, RAM included) |
| **code step** | A workflow step performed by a script. `gates.sh` / `pr-create.sh` / `pr-merge.sh`, plus the runner's built-in `sync-base` |
| **class** | A kind of work: judgment / research / coding. Resolved to a model name in `routes.env` |
| **dispatch** | The glue scheduler. Runs todo items in order with `kb run`. Plain code |
| **dry run** | A run that touches no VM: schema validation and prompt assembly only |
| **facts** | A `project.yml` field: project facts told to the agent every time |
| **gates** | The project's quality gates (lint / typecheck / test / static analysis). Run by code; red sends the agent back |
| **GitHub App** | `aifactory-sandbox`. Issues one-hour tokens for pushing and opening PRs from VMs |
| **glue** | Section 4: intake and dispatch |
| **human** | A transition target: hand over to a human and stop. Work is preserved on the wip branch |
| **installation token** | A one-hour, repository-scoped token issued by the GitHub App |
| **intake** | The glue importer. Turns free text into a ticket with one LLM call |
| **judgment** | A class: critical judgement. planner / reviewer / intake. Model is Fable |
| **kanban** | Section 2: ids, state, bodies. The `kb` CLI |
| **kb** | The kanban CLI |
| **known_red_gates** | A `project.yml` field: gates already red on base. The runner downgrades their FAIL to INFO |
| **PJ** | Project. Corresponds to one target repository. Defined in `$AIFACTORY_WORKSPACE/projects/<pj>/` (bundled examples in `examples/projects/<pj>/`) |
| **pool** | The set of VMs to lend. Default: 3 per project plus 3 generic |
| **provision.sh** | The baking steps for a project template |
| **project.yml** | The definition of a project's facts and policy |
| **release** | Roll a VM back to `clean` and return it |
| **reinject** | Re-inject tokens into a lent VM (no rollback) |
| **reset** | Roll a VM back to `clean`. Stays lent |
| **role** | An agent's role: planner / implementer / researcher / reviewer. Constitution in `roles/<role>.md` |
| **routes.env** | The class → model routing table |
| **run** | One execution of the runner. `$AIFACTORY_WORKSPACE/runs/<date>-<pj>-<id>/`. The kanban `run` column records only the directory name |
| **runner** | `workflow/bin/run`. The Python that reads a workflow definition and executes steps in the VM |
| **sandbox** | Section 1: the isolated execution environment (VMs) and its CLI |
| **sb-gw** | The gateway LXC (9000): Tailscale subnet router + dnsmasq + firewall |
| **sbnet** | The Proxmox SDN vnet, `10.77.0.0/16` |
| **step** | One invocation in a workflow: an agent step or a code step |
| **STOP** | The marker the planner writes at the top of a plan for dangerous or unclear requests |
| **take** | Lend one VM |
| **task-id** | The number (3 or more digits) assigned by kanban. Used in the VM name `task-<id>`, the branch and the run |
| **tmpfs** | An in-memory filesystem. `/run/sandbox/env` in the VM. Erased by rollback |
| **transition** | Where to go next depending on a step's result. `next` / `on_pass` / `on_fail`, with a loop limit |
| **wip branch** | `sandbox/<id>-<wf>-wip`. Where work is preserved when a run goes to human |
| **workspace** | Where operational data lives. `AIFACTORY_WORKSPACE` (default `<repo>/workspace/`, not tracked by git): `projects/` `kanban/` `runs/` `logs/` `docs/` |
| **workflow** | Section 3, and the per-kind procedure (YAML) |
| **section** | One of the four parts of the factory: sandbox / kanban / workflow / glue |
