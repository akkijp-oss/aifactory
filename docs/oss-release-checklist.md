# 公開リポジトリとして保つためのチェックリスト

2026-09-06 に、このリポジトリを Apache-2.0 の OSS として公開できる形に整えた（ADR-0016）。棚卸しと経緯の全文はメンテナの workspace 側にある。ここには**これからも守ること**だけを残す。

## 置き場の約束
- 枠組み（コード・kit・手順・ADR・サイト）はこのリポジトリ。運用データ（PJ 定義・チケット・実行記録・ログ・私有メモ）は `AIFACTORY_WORKSPACE`（既定 `workspace/`、git 追跡外）。置き場の判断は `lib/aifactory_paths.py` にだけ書く
- 公開してよい参照例は `examples/projects/` に置く。私有 PJ の名前・リポジトリ・認証情報は例にも書かない。例に使う PJ 名は同梱の `kumitate` か `<pj>`
- 環境固有の実値（ホスト名・LAN の IP・ノード構成・Tailscale の IP）はリポジトリに書かない。文書の例は `pve1`（例）や `PVE_HOST` のように変数で示す。sandbox 内の `10.77.0.0/16` は既定値として書いてよい
- 人名や「所有者の指示」という書き方ではなく「メンテナ」「メンテナの判断（日付）」と書く
- 他人の著作物（講演の文字起こし・動画）は入れない。出典（題名・話者・要旨）だけ書く

## 公開前・PR 前に回すもの
```bash
bin/oss-check.sh                                      # 追跡されてはいけない置き場 / 秘密情報 / 固有名 / 必須ファイル
python3 -m unittest discover -s console/tests && python3 -m unittest discover -s workflow/tests
cd website && .venv/bin/mkdocs build --strict -f mkdocs.yml
```
CI（`.github/workflows/ci.yml`）も同じことを PR ごとに回す。

## public に切り替える時に決めること
- [ ] 履歴: 過去のコミットには私有 PJ の実行記録が含まれる。public にする前に「履歴を squash した初期コミットで出す」か「そのまま出す」かを決める（推奨は squash。`git filter-repo` より安全で安い）
- [ ] 他人が clone して 1 周回せる公開サンプル PJ（`examples/projects/sample-app/` と、その対象になる小さな公開アプリ）。`kumitate` は私有リポジトリなので参照用に留まる
- [ ] GitHub 側: リポジトリを public に、Pages（`docs.yml` を手動起動）、Discussions / Issues の設定、`v0.1.0` タグと Release ノート（`CHANGELOG.md`）
- [ ] `gitleaks detect` か `trufflehog filesystem .` を 1 回
