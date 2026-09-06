# 貢献と AI セッション向けの約束

このリポジトリは Apache-2.0 で公開されています（[akkijp-oss/aifactory](https://github.com/akkijp-oss/aifactory)）。貢献は GitHub の pull request で受けます。人間（メンテナや貢献者）と複数の AI セッション（Claude Code）が交代で、ときに同時に作業する前提で書かれていて、人でも AI でも、作業の入り方は同じです。リポジトリ直下の `CONTRIBUTING.md`（貢献の手順）と `SECURITY.md`（脆弱性の報告先）が正本で、このページはその読み下しです。

## 現在地に立つ

1. ルート `README.md` で全体像（4 区画）を掴む
2. 着手する区画の `README.md` で設計と契約を読む
3. sandbox を触るなら `sandbox/STATUS.md` の実機確認コマンドを流して、書かれている進捗と実機が一致するか自分で確かめる。一致していなければ、先に `STATUS.md` を実機に合わせて直す
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
| 踏んだ罠 | `workflow/README.md` の「踏んだ罠と対処」 |
| 利用者向けの変更履歴 | `CHANGELOG.md` |
| 自分の環境のメモ | `$AIFACTORY_WORKSPACE/docs/`（リポジトリ外） |
| 人向けの読み下し | `docs/*.html`、このサイト（`website/`） |

## 変更の作法

| 変えるもの | 前に | 後に |
|---|---|---|
| workflow yml / roles / project.yml | `kb run <id> --dry-run` で schema 検証と依頼文を確認 | 次の run で効く。走っている run には触らない |
| `sandbox/bin/sandbox` | `bash -n` | `sandbox/bin/install.sh` で PATH のコピーを更新 |
| runner（`workflow/bin/run`） | 走っている run が無いことを確認（Python なので実行中の編集は安全だが、挙動の差が混ざる） | `python3 -m unittest discover -s workflow/tests` と `--dry-run` で確認 |
| console / mcp | `python3 -m unittest discover -s console/tests` | launchd 常駐なら `launchctl kickstart -k gui/$(id -u)/com.aifactory.console` |
| Proxmox 側スクリプト | `sandbox ls` で貸出中が無いことを確認 | `STATUS.md` を更新 |
| kanban / glue | `AIFACTORY_WORKSPACE=<別ディレクトリ>`（または `KB_ROOT`）でテスト | |
| このサイト | `website/content/ja/` を直し `en/` に訳す | `mkdocs build --strict` が通ること |

CI（`.github/workflows/ci.yml`）は pull request ごとに `console/tests` と `workflow/tests` の unittest と `mkdocs build --strict` を回します。手元で同じものを通してから PR にしてください。

## コミットと pull request

- 自分の変更だけを `git add` で選ぶ。`git add -A` は使わない
- メッセージは「何を・なぜ」を 1 行目に。本文に判断と実測を残す
- 実行記録（`workspace/runs/`）と台帳（`workspace/kanban/kanban.db`）は workspace にあり、リポジトリでは**追跡しない**。PR に含めない
- 変更は main へ直接 push せず、ブランチを切って pull request にする。マージは人間が判断する（agent は push しない）
- 設計を変える PR には ADR を 1 枚添える。利用者に見える変更は `CHANGELOG.md` に 1 行足す

## AI セッションへの補足

- 会話の冒頭で見た状態を信じない。着手直前に `git status` / `sandbox ls` / `ls docs/adr/` を取り直す
- 分からないことを推測で埋めない。実機とファイルで確かめる
- 人間に依頼が要る箇所（🧑）で止まり、何をしてほしいかを 1 行で書く
- 終わったら、何をして何が残っているかを `STATUS.md` か `ledger.md` に書いてから終える
- 秘密情報をログや報告に出さない。トークンはマスク表示で
