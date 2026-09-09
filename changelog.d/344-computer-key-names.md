### Fixed
- **computer の `key` で記号・ファンクション・特殊キーが使えるようになった**。`{"action":"key","keys":["CMD","SHIFT","="]}` が `unsupported key` で落ちていた。Mac / Windows / Linux で同じ 79 個のキー名（修飾キー・`ENTER` などの特殊キー・`F1`〜`F12`・`A`〜`Z`・`0`〜`9`・記号 `= - + , . / ; ' [ ] \ ` `）を受け付ける。`WIN` と `CMD` はどちらもその OS のメタキーの別名。

### Changed
- **受け付けないキー名のエラーに、受け付ける名前の一覧が付くようになった**（`unsupported key: <名前>; supported: ...`）。`computer` / `computer_action` の説明にも対応キーの一覧を載せたので、エージェントが名前を推測して送り直さなくて済む。キーが効かないときの代替手順は[画面操作のガイド](https://akkijp-oss.github.io/aifactory/ja/guides/computer-use/)にある。
