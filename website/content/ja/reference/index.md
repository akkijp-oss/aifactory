# リファレンス

コマンドと設定ファイルの全項目です。使い方の流れは [使い方](../guides/index.md)、意味は [仕組み](../concepts/index.md)。

## CLI

| コマンド | 区画 | 役割 | ページ |
|---|---|---|---|
| `kanban/bin/kb` | kanban | 採番・状態・履歴・runner の呼び出し | [kb](cli-kb.md) |
| `glue/bin/intake` / `glue/bin/dispatch` | glue | 自由文 → チケット / todo → 実行 | [intake / dispatch](cli-glue.md) |
| `workflow/bin/run` | workflow | runner 本体 | [run](cli-run.md) |
| `sandbox`（`sandbox/bin/sandbox`） | sandbox | VM の貸出・返却・トークン・GitHub App | [sandbox CLI](cli-sandbox.md) |
| `sandbox/proxmox/run.sh` | sandbox | Proxmox 側スクリプトの入口 | [sandbox CLI](cli-sandbox.md#proxmox) |

## 定義ファイル

| ファイル | ページ |
|---|---|
| `$AIFACTORY_WORKSPACE/projects/<pj>/project.yml`（例: `examples/projects/kumitate/`） | [project.yml](project-yml.md) |
| `workflow/kit/workflows/<name>.yml` | [workflow yml](workflow-yml.md) |
| `workflow/kit/roles/*.md`、`routes.env` | [役割とモデル](roles-and-models.md) |

## その他

- [ディレクトリ構成](directory-layout.md)
- [用語集](glossary.md)

## 実行環境

| 要素 | 値 |
|---|---|
| Mac 側の Python | 3.10 以上。`pyyaml` と `jsonschema` |
| `sandbox` CLI | bash。`jq` / `ssh` / `scp` / `curl` / `openssl` |
| VM | Ubuntu 24.04、`dev` ユーザー、Claude Code、mise（Node 22、PJ ごとの Ruby / Node） |
| Proxmox | 9.x、SDN、LVM-thin |
