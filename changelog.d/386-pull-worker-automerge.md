### Fixed
- **pull worker（macOS / Linux）の PJ で `auto_merge` を設定できるようになった**。ADR-0042 で automerge 工程が全 workflow に入って以降、`auto_merge` を書いた macos-pull / linux-pull の PJ は `unsupported pull-worker code steps: pr-automerge.sh` で run を 1 つも始められなかった。ゲートが緑・レビューが PASS・CI が緑のとき、ゲストの中の `gh` が PR を base へマージし、その事実が run の記録（`state.json` の `merged`）に残る。Windows のワーカーは引き続き自動マージに未対応で、`auto_merge` を書いた PJ は起動前に理由付きで拒否される。

### Added
- **workflow の code step が pull backend の対応表に載っているかを検証するテスト**（`workflow/tests/test_code_steps.py`）。新しい code step を足して macOS / Windows / Linux の対応表を更新し忘れると、そのワーカーの PJ が起動できなくなる（ADR-0042 で実際に起きた）。足すときの手順は[Mac ワーカーの手順書](docs/macos-worker.md)にある（ADR-0058）。
