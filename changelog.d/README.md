# changelog.d/ — 1 run 1 ファイルの変更履歴

`CHANGELOG.md` の `## [Unreleased]` を直接編集しない。ここに **1 ファイル 1 項目** を置く。

なぜ: 並列に走った run が全部 `## [Unreleased]` の同じ行に箇条書きを足すので、先にマージされた PR が 1 本あるだけで
後発の PR が必ず CONFLICTING になる（2026-09-08 に 7 本）。別ファイルなら git 的に衝突しない（ADR-0031）。

## 書き方

ファイル名は `<チケット番号>-<slug>.md`（例 `239-runner-sync-base.md`）。中身は Keep a Changelog の見出しと箇条書き:

```markdown
### Fixed
- **runner: PR を作る直前に base の最新を取り込む**。並列に走った別 run の PR が先にマージされても……
```

- 見出しは `Added` / `Changed` / `Deprecated` / `Removed` / `Fixed` / `Security`。1 ファイルに複数あってよい
- 利用者に見える変更だけ書く。内部だけの整理は書かなくてよい
- `CHANGELOG.md` は触らない（`bin/changelog-release check` がゲートで見ている）

## リリースのとき

```
bin/changelog-release collect --dry-run   # 何が Unreleased に載るか下見する
bin/changelog-release collect             # Unreleased に集約して changelog.d/*.md を消す
```

そのあと `## [Unreleased]` を `## [X.Y.Z] - YYYY-MM-DD` に閉じ、新しい空の `## [Unreleased]` を上に作って
`release: vX.Y.Z` のコミットにする。この README は集約の対象外なので消えない。
