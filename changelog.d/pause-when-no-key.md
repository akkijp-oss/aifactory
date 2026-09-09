### Changed
- **要る用途の Claude の鍵が鍵プールに無ければ、run は一時停止する**（ADR-0046）。`sandbox take` は runner から要る用途（`--need=fable,other`。workflow の agent step のモデルから決める）を受け取り、プールに無ければ VM を取らずに「鍵なし:」で止まる。プールに 1 本でも鍵があれば env の鍵（非推奨）は VM に渡さない。runner は `failure: "nokey"` と `needed_keys` を残し、kb はチケットを todo に戻す。鍵を「鍵」画面か `sandbox keys add` で登録すると、`dispatch --resume-paused`（5 分ごとの timer）が初めから回し直す。`kb resumable` に「鍵の登録待ち」として載る
- console の run の結果に「Claude の鍵が無いので一時停止しました（必要: …）」が出る
