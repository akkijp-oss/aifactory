### Added
- **コンソール: 「develop に着地済みだが、この制御系にまだ配備されていない変更」がボードで分かる**。制御系の checkout は `main` 追従（ADR-0042）なので、`develop` に着地した変更は `main` への昇格と `bin/ctl-update` までは効かない。既存の警告（チケット 337）は `origin/main` との比較なので、この状態を「食い違い無し」と表示していた。`overview` の `repo` に `bases`（PJ の `base_branch` ごとの `undeployed` / `only_here` / 直近のコミット）と `undeployed` を足し、ボードに「origin/develop に着地済みで、この制御系にまだ配備されていないコミットが N 件あります」と件名を出す（MCP の `overview` にも載る）。比べるのはこの checkout と同じ `repo` を持つ PJ だけ（ADR-0070）。
- **run: VM へ配った PJ 定義が `origin/<base>` の版と違うと、`work/gates.txt` の先頭に出る**。`INFO pj-drift examples/projects/aifactory/ differs from origin/develop: gates.sh (missing: unittest-pull)` の形で、**その run で走っていないゲートの名前**まで出す。ゲートの行が黙って減っても気づけるようにするため。赤ではない（配布経路の問題なので run は止めず、実装役へも戻さない）。

### Changed
- **コンソールが 5 分に 1 回だけ origin を取り込むようになった**。取り込まないと「着地した直後」を検知できないため。対象のブランチだけで、HEAD も作業ツリーも動かさない（timeout 15 秒・失敗しても他の機能は止まらず、ボードに 1 行出るだけ）。網や認証の都合で取り込めない運用は `CONSOLE_REPO_FETCH=0` で止められる（件数は最後に取り込めた時点のものになる）。
