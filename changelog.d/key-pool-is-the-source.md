### Changed
- **VM に渡す Claude の鍵は鍵プールが正本になり、`sandbox token set <pj> claude` は非推奨**（ADR-0045）。実行すると「鍵は `sandbox keys add` か console の「鍵」画面へ」という注意が出る（互換のため保存はする。`gh` はそのまま）。プールに用途の合う鍵があるときは env ファイルの鍵は使われない。main テナントでは PJ ファイルの鍵を全部プールへ移した
- `sandbox keys` と `sandbox token show` の言い方を console の「鍵」画面と揃えた（Fable に使う / Opus・Sonnet・Haiku に使う / 有効 / 登録日）。`keys list` の見出しは `FABLE OPUS/SONNET ENABLED …`
- console の sandbox 画面の「トークン」列を「Claude の鍵」列にし、出どころ（鍵プール / 鍵プール（片方の用途だけ） / PJ 別の設定ファイル（非推奨） / 全体の設定ファイル / 未設定）を出す。API `/api/sandbox` に `key_source` と `key_pool`
- README / OPERATIONS / BUILD / STATUS、website（ja/en の operations・add-project・tenants・install-mac・first-run・troubleshooting・configuration・cli-sandbox）を鍵プール前提に書き直した
