# 貢献と AI セッション向けの約束

aifactory は Apache-2.0 ライセンスで公開されており、[GitHub のリポジトリ](https://github.com/akkijp-oss/aifactory)でプルリクエストを受け付けています。人間も AI セッションも、以下の手順とルールに従って作業してください。

貢献の手順はリポジトリ直下の `CONTRIBUTING.md`、脆弱性の報告方法は `SECURITY.md` を基準とします。このページでは、その内容を日本語で説明します。

## 作業状況を確認する

1. ルート `README.md` で全体像（4 区画）を掴む
2. 着手する区画の `README.md` で設計と契約を読む
3. sandbox を触るなら `sandbox/STATUS.md` の実機確認コマンドを実行して、書かれている進捗と実機が一致するか自分で確かめる。一致していなければ、先に `STATUS.md` を実機に合わせて直す
4. 判断を変えたら `docs/adr/` に 1 枚追加する（既存 ADR を書き換えない）

## 守ること

- **実機が正**。ドキュメントと実機が食い違ったら実機を信じ、ドキュメントを直す
- 人間の操作が必要な箇所（Tailscale 承認、`claude setup-token` 等）は `BUILD.md` に 🧑 印で明示してある。そこで止まって依頼する
- 秘密情報（トークン・鍵）はこのリポジトリに書かない。置き場は `~/.config/sandbox/`（`sandbox/README.md` の「秘密情報」節）
- 個人環境の事実（ホスト名、LAN のアドレス、対象リポジトリの一覧）もリポジトリに書かない。workspace（`$AIFACTORY_WORKSPACE/docs/`）に置く。ドキュメントの例は同梱の `examples/projects/kumitate/` か汎用の `<pj>` / `myapp` を使う
- **同時に別の AI セッションが動いている前提**で振る舞う
    - ADR を足す前に `ls docs/adr/` で最大番号を取り直す
    - `git status` に自分が触っていない変更があっても戻さない。コミットは自分の分だけに絞る
    - 貸出中の sandbox VM を再起動・巻き戻し・作り替えしない。全 VM を触る作業は直前に貸出状態を読んで飛ばす
    - チケットの状態は `kanban/bin/kb` で更新する。runner を直接呼んだときも `kb sync` で追従させる
- 優先順位は**コストより品質**（メンテナの判断、2026-09-06）。安く済ませる工夫より、確実に動き・壊れず・後から読める作りを選ぶ

## どこに何を書くか

| 種類 | 置き場 |
|---|---|
| 現在地・未決・履歴 | `docs/ledger.md` |
| 各区画の設計と契約 | `<区画>/README.md` |
| 構築手順（コマンドと完了条件） | `sandbox/BUILD.md` |
| 進捗票と実機確認 | `sandbox/STATUS.md` |
| 運用（貸出・返却・障害） | `sandbox/OPERATIONS.md` |
| 設計判断の理由 | `docs/adr/NNNN-*.md` |
| 過去に起きた問題 | `workflow/README.md` の「踏んだ罠と対処」 |
| 利用者向けの変更履歴 | `changelog.d/<チケット番号>-<slug>.md`（`CHANGELOG.md` は直接編集しない） |
| 自分の環境のメモ | `$AIFACTORY_WORKSPACE/docs/`（リポジトリ外） |
| 利用者向けの解説 | `docs/*.html`、このサイト（`website/`） |

## 変更の作法

| 変えるもの | 前に | 後に |
|---|---|---|
| ワークフローの YAML / roles / project.yml | `kb run <id> --dry-run` でスキーマ検証と依頼文を確認 | 次の run で効く。実行中の run には触らない |
| `sandbox/bin/sandbox` | `bash -n` | `sandbox/bin/install.sh` で PATH のコピーを更新 |
| runner（`workflow/bin/run`） | 実行中の run がないことを確認（Python なので実行中の編集は安全だが、挙動の差が混ざる） | `python3 -m unittest discover -s workflow/tests` と `--dry-run` で確認 |
| console / mcp | `python3 -m unittest discover -s console/tests` | launchd 常駐なら `launchctl kickstart -k gui/$(id -u)/com.aifactory.console` |
| console の画面の文言 | `console/UX.md`（1 ページ: 摩擦の段階・ボイス・用語集）を読む。文言は `console/static/strings.js` に置き、`app.js` に直書きしない | `python3 -m unittest discover -s console/tests -p 'test_strings.py'` |
| Proxmox 側スクリプト | `sandbox ls` で貸出中がないことを確認 | `STATUS.md` を更新 |
| kanban / glue | `AIFACTORY_WORKSPACE=<別ディレクトリ>`（または `KB_ROOT`）でテスト | |
| このサイト | `website/content/ja/` を直し `en/` に訳す | `mkdocs build --strict` が通ること |

CI（`.github/workflows/ci.yml`）は pull request ごとに `console/tests` と `workflow/tests` の unittest と `mkdocs build --strict` を回します。手元で同じものを通してから PR にしてください。

## コミットと pull request

- 自分の変更だけを `git add` で選ぶ。`git add -A` は使わない
- メッセージは「何を・なぜ」を 1 行目に。本文に判断と実測を残す
- 実行記録（`workspace/runs/`）と台帳（`workspace/kanban/kanban.db`）は workspace にあり、リポジトリでは**追跡しない**。PR に含めない
- 変更は main へ直接 push せず、ブランチを切って pull request にする。マージは人間が判断する（エージェントは push しない）
- 設計を変える PR には ADR を 1 枚添える。ADR の番号は書く直前に `ls docs/adr/` で取り直す（並列に走る PR と番号がぶつかる）
- 利用者に見える変更は `changelog.d/<チケット番号>-<slug>.md` に 1 ファイル 1 項目で書く。`CHANGELOG.md` の `## [Unreleased]` は直接編集しない

## 変更履歴とリリース

`CHANGELOG.md` の `## [Unreleased]` は、全員が同じ行に箇条書きを足す場所なので、並列に走る PR が必ず衝突します。
そのため各 PR は `changelog.d/` にファイルを 1 枚置くだけにします（ADR-0031。書式は `changelog.d/README.md`）。

```markdown
<!-- changelog.d/239-runner-sync-base.md -->
### Fixed
- 利用者に見える変更を 1 項目
```

見出しは `Added` / `Changed` / `Deprecated` / `Removed` / `Fixed` / `Security`。リリースするときだけ、次の順で閉じます。

```bash
bin/changelog-release collect --dry-run   # 何が Unreleased に載るか下見する
bin/changelog-release collect             # Unreleased に集約し、changelog.d/*.md を消す
```

そのあと `## [Unreleased]` を `## [X.Y.Z] - YYYY-MM-DD` に閉じ、空の `## [Unreleased]` を上に作って `release: vX.Y.Z` のコミットにし、タグを打ちます。

## AI セッションへの補足

- 会話の冒頭で見た状態を信じない。着手直前に `git status` / `sandbox ls` / `ls docs/adr/` を取り直す
- 分からないことを推測で埋めない。実機とファイルで確かめる
- 人間に依頼が要る箇所（🧑）で止まり、何をしてほしいかを 1 行で書く
- 終わったら、何をして何が残っているかを `STATUS.md` か `ledger.md` に書いてから終える
- 秘密情報をログや報告に出さない。トークンはマスク表示で
