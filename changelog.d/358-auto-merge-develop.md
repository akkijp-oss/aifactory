### Added
- **ゲート緑・レビュー PASS・CI 緑の PR を runner がマージする**（`automerge` 工程）。`project.yml` の `auto_merge` を書いた PJ だけで動き、条件を 1 つでも満たさなければマージせず PR を開いたまま人間へ渡す（理由は run の記録・板・コンソールに 1 行残る）。マージした run はチケットが `done` になり、PR に「aifactory が自動マージした」のコメントが付く（ADR-0041）。

### Changed
- **aifactory 自身の PR の宛先が `develop` になった**。自動マージが入るのは `develop` で、`main` への昇格は人間が行う（手順は `sandbox/OPERATIONS.md`）。CI は `develop` への push でも走る。
