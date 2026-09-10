### Fixed
- **console: チケット詳細の実行記録で、走っている工程を「次は」と書かなくなった**。runner は工程を走らせている間 `next` を進めないので、実行中の run は `current.step` と `next` が同じになる。表の結果は `current` を先に見て「implement を実行中 3分」と出し、工程の切れ目（`current` が無い）だけ「次は gates（開始待ち）」と出す。run 詳細の「結果」も同じ判定にしたので、表と run 詳細で説明が食い違わない。工程の判定はボード・チケット・run 詳細で 1 か所（`stepNow` / `stepText`）に寄せた。
