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

## 済んでいること（2026-09-06）
- [x] 履歴: 私有 PJ の記録を含む旧履歴は持ち越さず、初期コミットから始めた（旧履歴はメンテナのローカルにだけ退避）
- [x] `gitleaks git`（全履歴）と `gitleaks dir workspace` で漏えい無しを確認
- [x] `v0.1.0` タグと Release（`CHANGELOG.md`）
- [x] リポジトリの説明・homepage・topics
- [x] 運用データ `workspace/` は私有リポジトリに分けてバックアップ（枠組み側からは見えない）

## public に切り替える時にやること
- [x] public に切り替え（2026-09-06）
- [x] GitHub Pages（Actions）でドキュメントサイトを公開: https://akkijp-oss.github.io/aifactory/ （更新は Actions の `docs` を手動起動）
- [x] org のトップページ: `akkijp-oss/.github` の `profile/README.md`
- [ ] 他人が clone して 1 周回せる公開サンプル PJ（`examples/projects/sample-app/` と、その対象になる小さな公開アプリ）。需要が出たら作る。`kumitate` は私有リポジトリなので参照用に留まる
- [ ] 公開後は `bin/oss-check.sh` を PR ごとに回す（CI には入っていない。固有名の一覧を CI に載せるのを避けるため）
