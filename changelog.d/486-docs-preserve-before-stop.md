### Changed
- **運用ガイド: ゲストを止める前の保全の読み方を足した**。Mac workerが停止直前に走らせる保全（ADR-0067）はログに `[preserve] …` としか出ず、`skipped` が異常なのかどうかも、`bin/run` 側の保全と何が違うのかも文書に無かった。`docs/macos-worker.md` に「ゲストを止める前の保全」の節を足し、3つのログ表記の読み分け・押す先のwipブランチ・2つの保全の違いと非対称の理由をまとめた。`workers/README.md` のpayload説明にも `preserve` を書いた。
