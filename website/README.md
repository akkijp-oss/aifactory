# website: aifactory ドキュメントサイト

リポジトリを初めて見た人が、導入・使い方・仕組み・リファレンスを一通り読めるようにした静的サイト。日本語（既定）と英語。公開先は https://akkijp.github.io/aifactory/ 。

- ジェネレータ: [MkDocs](https://www.mkdocs.org/) + [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) + [mkdocs-static-i18n](https://ultrabug.github.io/mkdocs-static-i18n/)
- 内容の置き場: `content/ja/`（正）と `content/en/`（訳）。同じファイル名で対にする
- 設定: `mkdocs.yml`（nav は日本語で書き、英語は `nav_translations` で対応づける）
- 図: Mermaid（Material が描画する。言語ごとにラベルを訳す）
- 正本はリポジトリ側の README / ADR / 各区画の README。サイトは「読み下し」なので、食い違ったらリポジトリを直してからサイトを直す

## ローカルで見る

```bash
cd website
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # 初回だけ
.venv/bin/mkdocs serve                                                # http://127.0.0.1:8000/ （英語は /en/）
```

## ビルド（静的ファイルを出す）

```bash
cd website
.venv/bin/mkdocs build --strict     # site/ に出る。--strict でリンク切れや nav 漏れがあれば失敗する
```

`site/` は git 追跡外（`.gitignore`）。CI（`.github/workflows/ci.yml`）も `mkdocs build --strict` を回すので、壊れたリンクは PR の段階で分かる。

## デプロイ

| 方法 | 手順 |
|---|---|
| GitHub Pages（本番） | `.github/workflows/docs.yml`。リポジトリ Settings → Pages → Source を「GitHub Actions」にしてから Actions タブで Run workflow。`mkdocs.yml` の `site_url` が公開 URL |
| Cloudflare Pages | Build command `pip install -r website/requirements.txt && mkdocs build -f website/mkdocs.yml`、Output directory `website/site`、Python 3.10 以上 |
| 任意の静的ホスティング | `mkdocs build` の `site/` を置くだけ。サーバー側の設定は不要（相対リンクで動く） |

## 書き方の約束

- 1 ページ 1 テーマ。最初の段落で「このページで分かること」を書く
- コマンドは実行できる形で書き、結果の例を添える
- ファイル名・コマンド名はリポジトリの実物に合わせる。変えたら両言語を直す
- 日本語を先に書き、英語は訳す。英語だけにある情報を作らない
- 例に使う PJ は同梱の `examples/projects/kumitate/`（akkijp/kumitate）か、汎用の `<pj>` / `myapp`。個人環境のホスト名や LAN のアドレスは書かない（`PVE_HOST` / `SB_NODE` などの変数で指す）
- `mkdocs build --strict` が通ることをコミット前に確認する
