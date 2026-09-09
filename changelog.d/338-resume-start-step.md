### Fixed
- **`kb run <id> --resume` が工程を 1 つも走らせずに VM を返却して終わることがあった**。再開する工程を `state.json` の `next`
  （失敗した run では必ず `human`）ではなく工程履歴から決めるようにした。準備（provision / prepare）が失敗して 1 工程も
  終えていない run は workflow の先頭工程から、履歴があれば最後に走った工程から続く（ゲートの戻しを使い切って止まった run は
  `gates` から。人が base を直してから続けられる）。続きが無い run（PR まで出ている、`next: end`）は VM に触る前に止まる（ADR-0047）
