### Fixed
- **pull backend（macOS）の鍵取得が `sandbox` を名前で呼ぶ**。GitHub トークンの発行（`sandbox gh-app token`）と Claude の鍵選び（`sandbox keys pick`）が repo 内の絶対パス（`<repo>/sandbox/bin/sandbox`）で起動していたので、テストが PATH に置いた偽 `sandbox` をすり抜けて本物が走り、#615 の「runner が呼ぶ外部コマンドの名前」の検査にも届いていなかった。他の呼び出し（`sandbox ssh` など）と同じ名前呼びに揃えた。
