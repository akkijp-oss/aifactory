### Fixed
- **macOS worker: ゲストを止める前に作業ブランチを wip へ push する**。operation の取り消しが worker に届いた回、worker はゲストごと停止するので、ゲスト内のコミット済み・未 push の実装が回収できずに消えていた（制御系側の保全は worker が busy で届かない）。停止の直前に `sandbox/<チケット>-<workflow>-wip` へ push するようにした。保全の成否にかかわらずゲスト停止は完了し、試行と結果は operation のログ（`[preserve] …`）に残る（ADR-0067）。
