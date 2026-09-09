### Added
- **コンソール: 制御系の checkout が origin と食い違っていると、ボードで分かる**。runner は PJ 定義（`examples/projects/<pj>/` の `project.yml` / `gates.sh` / `provision.sh`）を制御系の作業ツリーから直接読むので、そこで直して push していない変更はそのまま本番の挙動になる。`overview` に `repo`（`branch` / `upstream` / `ahead` / `behind` / `dirty` / `diverged`）を足し、食い違っているときはボードの先頭に「push していないコミット N 件」などと出す（MCP の `overview` にも同じものが載る）。判定は `console/lib/core.py` の `repo_status()` 1 か所で、`git fetch` はしない（ADR-0015）。

### Changed
- **`sandbox/OPERATIONS.md` と「日々の運用」に「PJ 定義の変更手順」の節を足した**。手元の checkout で直して push し、制御系では `bin/ctl-update` で配備するだけにする。制御系の中で直してしまったときの持ち出し（`git format-patch` → 手元から push → `git reset --hard origin/main`）と、制御系から直接 push できるようにする deploy key の付け替え手順も書いた。
