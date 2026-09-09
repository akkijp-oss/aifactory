### Added
- **人間が run の後始末（wip ブランチから PR を作ってマージ・打ち切り）をしたことを実行記録に残せる**。`kb run-note <run> [--result done|abandoned] [--pr N] [--text T]`（コンソールは `POST /api/runs/<name>/action`、MCP は `run_action`）が `state.json` に `human: {at, by, result, pr_url, text}` を足す。runner が確定した `result` は変わらない（ADR-0039）
- **チケットに PR 番号を入れる・完了にすると、紐づく run にも転記する**。`kb set <id> --pr N` / `kb done <id>`（コンソールのチケット画面・MCP の `ticket_action` も同じ道）で、その run が人間待ちのままなら「人間が仕上げた」が記録される
- **run 画面が「人間が PR #n で仕上げました（完了）。」と読めるようになった**。後始末の済んだ run には、続きから回すコマンドを出さない

### Fixed
- **同じチケットを回し直すと、チケットのメモが「再走中（attempt N・workflow W）」に替わる**。前回人間待ちで終わったときの「人間へ（wip: …）…」が実行中のメモとして残り、一覧が古い状態に見えていた
