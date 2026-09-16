### Added
- **票が「起票時の base sha」を持ち、run が古さを依頼文の 1 行で知らせる**。`kb new` が PJ の `base_branch` の
  tip commit を `gh` で自動記録し（`--base-sha SHA` で明示・`''` で消せる）、`kb run` が runner へ渡す。
  runner は VM で `git rev-list --count` を実行し、base が進んでいれば「## チケット」の直下に
  `- 注意: この票の行番号は N commits 前（<sha>）のもの。着手時に必ず自分で数え直すこと。` を 1 行だけ出す。
  距離 0 の票と sha を持たない既存票では 1 行も足さない。sha を取れない環境では起票は落とさず、
  stderr に理由が 1 行出るだけ（ADR-0089）。
