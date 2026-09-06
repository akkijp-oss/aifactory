### Fixed
- **macos-pull / linux-pull: humanに落ちたrunで作業ブランチを退避する**。ゲートの戻せる回数を使い切ってhumanに落ちたとき、pull backendは作業ブランチをpushせずにゲストを削除していたので、実装のコミットが失われ人が引き取れなかった。Proxmox backendと同じく、成果物回収の前に作業ブランチのHEADを `sandbox/<チケット番号>-<workflow名>-wip` へforce pushし、`state.json` の `wip_branch` に記録する。pushできない場合は `wip.patch` を残し、いずれの場合も成果物回収とゲスト削除は続行する。
