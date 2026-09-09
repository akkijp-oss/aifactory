### Fixed
- **macos-pull / windows-pull: 成果物の回収で対象外のものがあってもVMを返却する**。作業ディレクトリ直下にサブディレクトリ・symlink・合計4 MiBを超えるファイルがあると回収が例外で止まり、全工程がPASSした後でもゲストが削除されず予約（lease）がワーカーを塞いでいた。回収対象外は飛ばして名前と理由を `state.json` の `artifacts_skipped` に記録し、返却まで進むようにした。
