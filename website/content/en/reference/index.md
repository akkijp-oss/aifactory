# Reference

Every command and every configuration field. For the flow of use see the [Guides](../guides/index.md); for the meaning see [Concepts](../concepts/index.md).

## CLI

| Command | Section | Role | Page |
|---|---|---|---|
| `kanban/bin/kb` | kanban | Ids, state, history, calling the runner | [kb](cli-kb.md) |
| `glue/bin/intake` / `glue/bin/dispatch` | glue | Free text → ticket / todo → run | [intake / dispatch](cli-glue.md) |
| `workflow/bin/run` | workflow | The runner | [run](cli-run.md) |
| `sandbox` (`sandbox/bin/sandbox`) | sandbox | VM lending and return, tokens, GitHub App | [sandbox CLI](cli-sandbox.md) |
| `sandbox/proxmox/run.sh` | sandbox | Entry point for the Proxmox-side scripts | [sandbox CLI](cli-sandbox.md#proxmox) |

## Definition files

| File | Page |
|---|---|
| `$AIFACTORY_WORKSPACE/projects/<pj>/project.yml` (example: `examples/projects/kumitate/`) | [project.yml](project-yml.md) |
| `workflow/kit/workflows/<name>.yml` | [workflow yml](workflow-yml.md) |
| `workflow/kit/roles/*.md`, `routes.env` | [Roles and models](roles-and-models.md) |

## Other

- [Directory layout](directory-layout.md)
- [Glossary](glossary.md)

## Runtime

| Element | Value |
|---|---|
| Python on the Mac | 3.10 or later, with `pyyaml` and `jsonschema` |
| `sandbox` CLI | bash, with `jq` / `ssh` / `scp` / `curl` / `openssl` |
| VM | Ubuntu 24.04, user `dev`, Claude Code, mise (Node 22, per-project Ruby / Node) |
| Proxmox | 9.x, SDN, LVM-thin |
