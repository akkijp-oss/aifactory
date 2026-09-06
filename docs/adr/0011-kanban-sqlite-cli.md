# ADR-0011: kanban はリポジトリ内の SQLite + CLI（`kb`）。外部システムは取り込み口として後から足す

日付: 2026-09-06 / 状態: 採用 / 決定者: メンテナ（Claude Code の推奨を採択）

## 状況
- sandbox（区画1）と workflow（区画3）が実機で動き、チケットは `workflow/tickets/*.md` に手で置き、task-id も手で振っていた（101〜109、201〜202）
- kanban（区画2）の実体は未決で、候補が 3 つあった: 外部の個人メモにある個人タスク台帳（SQLite + CLI）/ termboard の Intent / 発注者側の Notion チケット DB
- kanban に求めるのは「task-id の採番」「状態管理」「入口」の 3 つ。動画の位置づけは「ただのコード、エージェント不在」

## 決定
- `kanban/` に CLI（`bin/kb`、Python 3 標準ライブラリのみ）、自前の SQLite 台帳（`kanban.db`）は運用データとして `$AIFACTORY_WORKSPACE/kanban/` に置く（起票時は `kanban/kanban.db` で git 追跡。2026-09-06 の公開化で workspace へ出し、git 追跡外に）
- 状態は DB、本文はファイル（`$AIFACTORY_WORKSPACE/kanban/tickets/<id>-<pj>-<slug>.md`）。runner には本文のパスをそのまま渡す
- `kb run <id>` が runner を呼び、`runs/<run>/state.json` から状態を進める。`kb sync` で手動 run にも追従する
- 個人タスク台帳 / termboard / Notion は **取り込み口**（外 → `kb new`）として glue と一緒に足す。kanban の正本にはしない

## 理由
- 個人タスク台帳は ID が `#101〜` で、既に使った sandbox の task-id と衝突する。粒度も「提案書を出す」「モックを作る」といったメンテナ個人のタスクで、ファクトリーのチケット（PJ の 1 修正）と違う。混ぜると両方が読みにくくなる
- termboard / Notion は別システム。kanban の契約（採番・状態）を外部の可用性と API に依存させたくない。入口が増えるのは歓迎だが、正本は 1 つでリポジトリ内にある方が、人間と AI セッションが交代で読むこの repo の前提に合う
- 本文をファイルにしておくと、runner の入力形式（1 行目が題名、`pr: N`）がそのまま使え、diff で読める。個人タスク台帳の「SQLite 正本 + Markdown 自動生成」と同じ型
- 「ただのコード」で済む範囲に留める。LLM が要る判断（種別の分類）は glue のルーターに置く

## 結果（トレードオフ）
- 良い: 外部依存ゼロ、runner との結線が最短、採番の一意性が DB で保証される、履歴が残る
- 悪い: 発注者（PJ 側の Notion 等）から直接は見えない。見せるには取り込み口と逆方向の反映が要る
- 悪い: DB は単一 Mac 前提。複数マシンで書くと衝突する（当面はメンテナの Mac だけなので受け入れる）
- 廃止: `workflow/tickets/` ディレクトリ（本文は kanban の `tickets/`（現 `$AIFACTORY_WORKSPACE/kanban/tickets/`）へ移動、run ディレクトリの `ticket.md` はそのまま）
