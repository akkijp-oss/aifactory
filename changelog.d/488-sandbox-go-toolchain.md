### Added
- **sandbox の aifactory テンプレートに Go を焼き、版は `workers/go.mod` から読む**。`workers/` を触る run で
  実装役が毎回 Go 自体を落としていた（2026-09-13 に 2 回）。`bin/go-toolchain.sh`（`required` / `url` / `check` /
  `install` / `ensure`）に導入と確認をまとめ、`provision.sh` が焼き、貸出直後の `prepare` が
  「焼いた Go が今の `go.mod` の要求を満たすか」を確かめる。満たせないまま進めず、入れ直せない run は
  `failure: prepare` で人へ返る（ADR-0071）。
