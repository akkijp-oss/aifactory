### Added
- **runner が新しい外部コマンドを呼び始めたら CI が名指しで落ちる**。`workflow/bin/run` と `workflow/lib/*.py` が
  `subprocess` / `sh()` / `stream()` に渡す argv[0] を構文木で集め、テストが PATH で偽装している名前の集合
  （`sandbox` / `scp` / `gh` と、VM に到達しないローカル実行の `bash`）に無い名前が増えたら失敗する検査を足した。
  偽装されていない名前は PATH の偽物をすり抜けて本物に届き、到達しない宛先で timeout ぶん止まる（実際に CI の
  test job が 21 分で打ち切られた）。失敗の文言に「既存の口（`sb()` / `run_remote()`）を使うか、偽装の側にも足す」
  という次の一手が出る。argv[0] がリテラルで取れない呼び出しは失敗にせず件数だけ出す。
