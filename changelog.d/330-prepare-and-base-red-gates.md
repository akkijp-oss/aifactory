### Added
- **`project.yml` に `prepare`（貸出直後の準備）**。VM を取って checkout した直後に PJ 固有のスクリプトを 1 回だけ走らせ、テンプレートを焼いた時点から base が進んだぶんの環境のずれ（依存の追加、DB migration）を揃える。失敗したら agent を起動せず `failure: prepare` で終わるので、直せない環境の赤に VM 時間を使わない（ADR-0037）。
- **ゲートが赤いとき、runner が同じゲートを base でも回して切り分ける**。base でも赤いゲートは `INFO <名前> red (also red on base; not a gate)` に落とし、実装役へ戻さず先へ進める。base での結果とログ末尾は `gates.txt` の `=== base check:` 以降に残り、確かめたゲート名は run の記録と `sandbox_status` の `known_red_gates` に載る（ADR-0037）。
