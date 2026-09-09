/* aifactory console の文言。画面に出る日本語はここに集める（console/UX.md）。
   本体は厳密な JSON（tests/test_strings.py が読み、表記のルールと app.js / index.html との鍵の対応を検査する）。
   - btn.* とダイアログの ok: ユーザーの行為を動詞で（「実行する」）。「OK」「はい」は使わない。取り消しは「キャンセル」
   - msg / err / empty / help / sub / next / banner とダイアログの本文: 「ですます」の文。末尾は「。」
   - 用語は用語集どおり（チケット / PJ / 実行記録 / 工程 / ジョブ / 貸出）。「タスク」「プロジェクト」は使わない */
const T = {
  "status": { "todo": "未着手", "in_progress": "実行中", "review": "レビュー待ち", "blocked": "人間待ち", "done": "完了" },
  "jobState": { "running": "実行中", "done": "終了", "failed": "失敗", "stopped": "止めた", "lost": "記録なし", "ended": "終了（終了コード不明）" },
  "result": { "end": "終了", "human": "人間へ", "failed": "失敗（開始前）", "abandoned": "中断" },
  "time": { "sec": "{n} 秒", "min": "{n} 分", "hourMin": "{h} 時間 {m} 分", "unknown": "時刻の記録なし", "ahead": "開始が未来の時刻" },
  "nav": { "board": "ボード", "tickets": "チケットの一覧", "intake": "起票", "runs": "実行記録", "jobs": "ジョブ", "sandbox": "sandbox", "stats": "統計", "keys": "鍵", "logs": "ログ", "config": "設定",
           "badgeScope": "この数字はすべての PJ の件数です。ボードで PJ を選んでも変わりません。", "updated": "更新 {t}（{tz}）", "tzDiffers": "記録の時刻は {tz} です。画面はこのブラウザーの時間帯に直しています。", "shortcuts": "? でショートカット" },
  "conn": { "on": "接続中", "off": "切断" },
  "power": { "running": "起動中", "stopped": "停止中", "idle": "節電で停止中", "candidate": "停止候補" },
  "banner": { "offline": "サーバーに届きません。console/bin/console が動いているか確かめてください。" },

  "btn": {
    "cancel": "キャンセル", "close": "閉じる", "undo": "元に戻す", "draftClear": "下書きを捨てる",
    "file": "起票する", "attach": "添付する", "detach": "添付を消す", "dispatch": "配車する", "run": "実行する", "dryRun": "dry-run で依頼文だけ確かめる",
    "save": "保存する", "sync": "実行記録に状態を合わせる", "intake": "取り込む",
    "refreshVms": "一覧を取り直す", "release": "返却する", "stop": "止める",
    "keyAdd": "この鍵を登録する", "keyRemove": "削除する", "keyToken": "トークンを入れ替える",
    "start": "開始にする", "review": "レビュー待ちにする", "done": "完了にする", "reopen": "未着手に戻す", "redo": "未着手に戻す（やり直す）", "block": "人間待ちにする",
    "openReason": "理由を読む", "openReport": "報告を読む", "openLatestRun": "最新の実行記録を開く",
    "openTickets": "一覧で探す", "openTicket": "チケットを開く", "openRun": "実行記録を開く", "openRuns": "実行記録の一覧を見る", "openSandbox": "sandbox を見る", "openBoard": "ボードへ戻る", "openIntake": "起票へ戻る", "openJob": "ジョブを開く"
  },

  "h": {
    "outcome": "結果", "artifacts": "成果物", "stepLogs": "工程のログ", "otherFiles": "その他のファイル（{n} 件）",
    "run": "runner で回す", "move": "状態を進める", "fix": "項目を直す", "runs": "実行記録", "jobs": "このコンソールのジョブ", "body": "本文", "attachments": "添付", "history": "履歴",
    "track": "工程", "files": "ファイル", "lent": "貸出中", "pjPool": "PJ とプール", "lsResult": "sandbox ls の結果",
    "keys": "登録してある鍵", "keyAdd": "鍵を登録する",
    "intakeFree": "文章から整えて起票する", "intakeNew": "題名と完了条件を自分で書いて起票する", "next": "次にすること", "output": "出力",
    "routes": "モデルの経路", "thisConsole": "この console", "shortcuts": "キーボードの近道", "rawLog": "元のログを見る",
    "byModel": "モデル別", "byStep": "工程別（工程 × モデル）", "byDay": "日別", "byPj": "PJ 別", "topSteps": "費用換算の高い工程（上位 20）", "howToRead": "読み方"
  },

  "th": {
    "run": "実行記録", "ticket": "チケット", "workflow": "workflow", "started": "開始", "elapsed": "所要", "result": "結果", "step": "工程",
    "at": "日時", "field": "項目", "before": "前", "after": "後", "state": "状態", "what": "内容", "rc": "終了コード",
    "process": "処理", "reason": "理由", "title": "題名", "updated": "更新", "lentSince": "貸出から", "token": "Claude の鍵", "lent": "貸出", "command": "コマンド", "pidRc": "プロセス ID / 終了コード", "name": "名前", "flow": "流れ",
    "lentTo": "貸出先", "power": "稼働状態",
    "poolDefined": "定義", "poolActual": "実体", "free": "空き",
    "model": "モデル", "steps": "工程数", "turns": "ターン", "avgTurns": "平均ターン", "avgMin": "平均分", "input": "入力", "cacheWrite": "キャッシュ書込", "cacheRead": "キャッシュ読出", "output": "出力",
    "cost": "費用換算", "avgCost": "1 工程あたり", "thinking": "thinking", "tools": "ツール呼出", "date": "日付", "minutes": "分", "log": "ログ",
    "keys": "鍵", "keyFable": "Fable に使う", "keyOther": "Opus・Sonnet に使う", "keyEnabled": "有効", "keyTail": "トークンの末尾",
    "issued": "登録日", "lastUsed": "最後に使った日時", "uses": "使用回数", "keyInUse": "使用中のチケット"
  },

  "label": {
    "q": "番号・題名", "qPlaceholder": "204 や 起票 のように", "status": "状態", "allStatus": "すべて",
    "pj": "PJ", "allPj": "すべて", "pjIfKnown": "PJ（分かっていれば）", "letLlm": "LLM に決めさせる", "kind": "種別", "workflow": "workflow", "option": "オプション",
    "workflowAsKind": "{kind}（種別のまま）", "keep": "終了後も VM を返却しない（中を見る）", "resume": "貸出中の VM で続きから（--resume）",
    "pr": "PR 番号", "prForMerge": "PR 番号（merge-pr のとき）", "note": "メモ", "notePlaceholder": "何を待っているか / 何をしたか",
    "blockNote": "何を待っていますか", "blockPlaceholder": "例: 本番 DB の権限を管理者に依頼中",
    "count": "件数", "count1": "1 件だけ", "count3": "3 件まで", "count10": "10 件まで", "countAll": "未着手が尽きるまで",
    "dispatchDry": "dry-run（VM を触らず、状態も進めません）", "intakeDry": "起票せず、判定だけ見る",
    "attachments": "添付するファイル", "dropHere": "ここにファイルを落とすか、選んでください",
    "request": "依頼文（音声の書き起こし、チャットの貼り付け、箇条書き、何でも）", "requestPlaceholder": "例: seeds が今のモデルに合っていなくて db:seed が落ちる。直してほしい",
    "title": "題名（1 行目になり、ブランチ名と PR の題名に使います）", "titlePlaceholder": "fix: … / docs: … / feat: …", "body": "本文（Markdown。末尾に「## 完了条件」を箇条書きで）",
    "bodyPlaceholder": "## 背景\n何に困っているか、どこで起きるかを書きます。\n\n## 完了条件\n- [ ] テストが緑になる\n- [ ] PR ができている",
    "runsAll": "dry-run と退避分（-attemptN）も見る", "fetching": "取得中", "vacant": "空き",
    "tid": "チケット番号", "tidPlaceholder": "205 のように", "logSrc": "種類", "allLogSrc": "すべて",
    "period": "期間", "includeDry": "dry-run も含める",
    "keyName": "名前（この画面と記録に出る呼び名。英数字と . _ - で 40 文字まで）", "keyNamePlaceholder": "例: max-akki、team-fable",
    "keyNote": "メモ（誰の契約か、どのプランか、など）", "keyNotePlaceholder": "例: akki の Max プラン",
    "keyToken": "トークン（claude setup-token で表示される、sk-ant-oat01- で始まる文字列。登録後は表示しません）",
    "keyAllowFable": "Fable に使う（計画・設計・レビューの工程）", "keyAllowOther": "Opus・Sonnet・Haiku に使う（実装・調査の工程）"
  },

  "stats": {
    "period": { "1": "今日", "7": "7 日", "30": "30 日", "all": "全部" },
    "tile": { "steps": "工程", "turns": "ターン", "cacheRead": "キャッシュ読出", "output": "出力", "cost": "費用換算", "thinking": "thinking のある工程" },
    "thinkingCell": "{n} 回", "thinkingVisible": "本文あり {v} 回 / {c} 字", "visibleChars": "見える文字 {c}", "share": "全体の {p}", "maxOf": "最大 {cost}", "noResult": "result なし {n}", "rateLimited": "利用枠で拒否 {n}",
    "scope": "{sel} 工程（記録は全部で {all}）"
  },

  "keys": { "count": "{n} 本", "never": "まだ使っていません" },

  "sub": {
    "stats": "agent の工程ごとに、ターン・トークン・thinking・時間・費用換算を実行記録から集めます。まず、どこで消費しているかを知るための画面です。",
    "board": "未着手 → 実行中 → レビュー待ち → 完了。人間待ちは横に置きます。",
    "tickets": "番号・題名・PJ・状態で探せます。完了したチケットもすべてここに並びます。",
    "runs": "runner がチケットを 1 回回した記録です。工程ごとのログと成果物をここから読めます。記録は runs/ に残ります。",
    "sandbox": "貸出は誰がその VM を使っているか、稼働は VM の電源が入っているかです。稼働の一覧は Proxmox に ssh して取ります（数秒）。",
    "intake": "依頼をチケットとして登録します。左は文章を LLM が題名と完了条件に整えます。右は自分で書いた題名と本文をそのまま登録します。登録するだけで、実行はまだ始まりません。",
    "jobs": "この画面から押した起票・実行・配車・返却の 1 回ごとの記録です。出力と終了コードをここから読めます。記録は console/jobs/ に残ります。",
    "logs": "起票と配車の記録です。チケット番号や PJ で絞り込めます。番号を押すとチケットへ移れます。原文は下の「元のログを見る」で読めます。",
    "config": "読むだけの画面です。変えるときはファイルを編集してください。",
    "keys": "Claude のトークン（claude setup-token で作る長期トークン）を「鍵」として登録しておく場所です。チケットを実行するとき、ここから鍵を 1 本選んで VM に渡します。鍵を何本か登録しておくと、利用枠の消費を分散できます。"
  },

  "board": {
    "liveStep": "{step} を実行中 {t}", "liveNext": "次は {step}", "liveSince": "（開始から {t}）", "jobsRunning": "ジョブ {n} 件が実行中",
    "runsCount": "実行記録 {n} 件（うち開始前 {m} 件、中断 {a} 件）", "liveAbandoned": "中断。runner は {t} に終わっています。", "ticketCount": "左の数字はチケットの件数です。",
    "noLive": "動いている run はありません。", "more": "ほか {n} 件をすべて見る", "moreRuns": "ほか {n} 件の動いている run を実行記録で見る",
    "scopeAll": "集計と列の対象: すべての PJ", "scopePj": "集計と列の対象: PJ {pj}（左のナビの数字はすべての PJ）",
    "repoDiverged": "{path} が origin と食い違っています（{what}）。", "repoAhead": "push していないコミット {n} 件",
    "repoBehind": "取り込んでいないコミット {n} 件", "repoDirty": "未コミットの変更 {n} 件",
    "repoHow": "PJ 定義はこの作業ツリーから読むので、直したときは sandbox/OPERATIONS.md の「PJ 定義の変更手順」で origin に反映してください。"
  },
  "tickets": { "count": "{n} 件（全 {m} 件）" },
  "logs": {
    "count": "{n} 件（全 {m} 件）", "capped": "新しい {n} 件だけ表示しています。", "confidence": "確度 {v}",
    "endDetail": "終了コード {code}・所要 {t}", "dryRun": "dry-run",
    "source": { "intake": "起票", "dispatch": "配車" },
    "event": { "intake": "起票", "start": "開始", "end": "終了", "blocked": "人間待ちにした", "skip": "飛ばした", "idle": "未着手なし", "other": "その他" },
    "reason": { "worker_unavailable": "worker が空いていません", "pool_busy": "プール {n} 台すべて貸出中" }
  },
  "ticket": { "crumb": "チケット {id}", "dbRun": "台帳に記録された run:", "stamps": "作成 {c} / 更新 {u}" },
  "intake": { "pjReadyBadge": "実行できます", "pjNotReadyBadge": "準備が必要", "checkSandbox": "sandbox で準備状態を見る" },
  "run": {
    "nextStep": "次は {step}", "elapsed": "{t} 経過", "plan": "定義:", "wip": "退避", "loops": "戻し", "v0": "v0 の記録（Markdown 1 枚）です。",
    "truncated": "末尾 300 KB だけ表示しています。", "following": "{step}（{kind}）の出力を追い読みしています。", "refresh": "5 秒ごとに更新します。",
    "notStarted": "開始前（記録なし）", "error": "失敗の理由", "runnerGone": "runner は終了",
    "noState": "state.json がありません。工程が始まる前に止まった run です。今の状態はチケットで確かめてください。",
    "stateBroken": "state.json を読めませんでした。工程と時刻は分かりません。今の状態はチケットで確かめてください。",
    "noStarted": "開始時刻は記録にありません。",
    "planHelp": "各工程が何をするか", "planCode": "機械", "planReads": "読む: {files}", "planWrites": "書く: {files}",
    "planOnFail": "通らなければ {step} に戻します（最大 {n} 回。超えたら人間待ち）。", "planOnFailHuman": "通らなければ人間待ちになります。",
    "planBrief": "この workflow での指示: {brief}", "planUnknown": "この工程の説明はまだありません。workflow の定義を確かめてください。"
  },
  "step": { "wait-vm": "VM の空き待ち" },
  "stepDesc": {
    "research": "チケットに関係する既存コード・過去の決定（docs / ADR）・外部仕様を集め、research.md にまとめます。",
    "design": "調査を踏まえて設計し、この PR で入れる範囲を plan.md に書きます。",
    "plan": "何をどう直すかを決め、計画を plan.md に書きます。",
    "implement": "計画に沿ってコードを変えてコミットし、やったことを report.md に書きます。",
    "gates": "PJ のゲート（テスト・lint など）を VM で走らせ、結果を gates.txt に残します。赤があれば実装に戻します。",
    "review": "計画との整合と、既存の約束を破っていないかを見て、判定を review.md に書きます。",
    "sync": "PR を作る直前に base ブランチを取り込みます。衝突や ADR 番号の重複があれば解消の工程に回します。",
    "resolve": "base の取り込みで戻された理由（衝突・ADR 番号の重複）だけを解消します。本来の変更は広げません。",
    "pr": "成果物をまとめて PR を作り、人間のレビューに渡します。",
    "automerge": "条件（ゲート緑・レビュー PASS・CI 緑・衝突なし）を確かめ、満たせば PR を base へマージします。",
    "merge": "PR ブランチへ push し、base へマージします。",
    "judge": "調査の要点と次の一手を判断して summary.md に書きます。"
  },
  "outcome": {
    "pr_created": "PR ができました。次はレビューです。",
    "merged": "aifactory が PR #{pr} を {base} へ自動マージしました（完了）。",
    "merged_nopr": "aifactory がこの run の PR を {base} へ自動マージしました（完了）。",
    "automerge_skipped": "自動マージはしませんでした: {why}。PR は開いたままです。",
    "loop_limit": "工程 {step} が {n} 回続けて通らず、人間待ちになりました。",
    "step_failed": "工程 {step} で止まりました。",
    "step_timeout": "工程 {step} が時間上限 {n} 分で中断されました。コミット済みの分は wip ブランチに残っています。",
    "quota_paused": "工程 {step} が Claude の鍵の利用枠の上限（{type}）で中断されました。途中までの変更は wip ブランチに残っています。{when} 以降に自動で続きから再開します。",
    "quota_paused_unknown": "工程 {step} が Claude の鍵の利用枠の上限（{type}）で中断されました。途中までの変更は wip ブランチに残っています。解除時刻は記録に無く、しばらく待ってから自動で続きから再開します。",
    "quota_exceeded": "工程 {step} が Claude の鍵の利用枠の上限で {n} 回続けて中断されました。自動再開は止めています。鍵の枠を確かめてください。",
    "key_failed": "工程 {step} で Claude の鍵が使えず中断されました: {summary}。鍵を直してから続きを回してください。",
    "gateFails": "赤いゲート: {gates}。",
    "ended": "すべての工程が終わりました。",
    "waiting": "工程は終わり、人間の判断を待っています。",
    "human_done": "人間が PR #{pr} で仕上げました（完了）。",
    "human_done_nopr": "人間がこの run を引き取って仕上げました（完了）。",
    "human_abandoned": "人間がこの run を打ち切りました。",
    "humanNote": "{by} が {at} に記録: {text}",
    "resume": "同じチケットを続きから回すには {cmd} を実行します。",
    "running": "工程 {step} を実行中です。",
    "unknown": "止まった理由は記録にありません。工程のログを確かめてください。",
    "runner_gone": "runner は {end} に終わっています。待っても進みません。",
    "runnerJob": "ジョブ {label} は{state}で、終了コードは {rc} です。",
    "failed_before_start": "VM の準備で止まりました: {summary}",
    "wait_timeout": "VM の空きを {n} 分待ちましたが出ませんでした。チケットは未着手に戻っています。",
    "prepare_failed": "貸出直後の準備（prepare）で止まりました: {summary}",
    "lease": "VM {name} は貸出中のままです。",
    "noDetail": "理由を書いたファイルは残っていません。",
    "ticketNow": "この run は {end} に終わりました。チケット {id} の今の状態は「{status}」です（{at} 更新）。",
    "ticketNewerRun": "チケット {id} の最新の実行記録は {run} です。",
    "noTicket": "この run に対応するチケットはありません。"
  },
  "artifact": {
    "ticket": "チケット", "researcher": "調査", "planner": "計画", "implementer": "報告", "reviewer": "レビュー", "judge": "まとめ", "gates": "検証結果"
  },
  "sandbox": {
    "count": "{n} 台", "countShared": "{n} 件（VM は {m} 台）", "sharedBadge": "共有",
    "sharedWith": "チケット {tasks} と同じ VM です。",
    "sharedWarn": "VM {vm}（{vmid}）がチケット {tasks} に同時に貸出中です。同じ VM なので 2 台ではありません。実際の重複か表示のずれかを「一覧を取り直す」で確かめ、どのチケットの作業を残すか決まるまで返却しないでください。",
    "leasesOnPool": "貸出 {n} 件", "runOn": "run が動いています（工程 {step}）", "yes": "あり", "no": "なし",
    "keySource": { "pool": "鍵プール", "pool_partial": "鍵プール（片方の用途だけ）", "pool_partial_nofallback": "鍵プール（片方の用途だけ）", "pj": "PJ 別の設定ファイル（非推奨）", "global": "全体の設定ファイル", "none": "未設定" },
    "lsAt": "{t} 取得", "lsNever": "まだ取っていません", "lsFailed": "{t} に取れませんでした",
    "actualAt": "実体は {t} 取得", "actualStale": "実体は {t} 取得（{n} 分前）", "actualNever": "実体はまだ取っていません",
    "actualUnknown": "未取得", "unbuilt": "未構築 {n} 台。proxmox/40-pool.sh {pj} {n} で足せます。"
  },
  "job": { "following": "2 秒ごとに追い読みしています。" },
  "config": { "roles": "役割:" },
  "kind": {
    "bug": "動きが期待と違うときに選びます。再現するテストを先に書いてから直し、PR まで進みます。",
    "chore": "判断のいらない小さな作業に選びます。計画を省いて、実装から始めます。",
    "hotfix": "本番が止まっているときに選びます。原因の箇所だけを最小に直し、レビューを厳しくします。",
    "feature": "新しい機能や画面を足すときに選びます。調査と設計のあとに実装します。",
    "feature-long": "変更が大きい機能を足すときに選びます。feature と同じ流れで、実装の時間上限を 180 分に延ばします。",
    "docs": "ドキュメントを書く・直すときに選びます。実装を確かめて構成を決めてから執筆します。",
    "research": "調べてまとめるだけのときに選びます。コードは変えず、調査と要点の 2 枚を残します。",
    "merge-pr": "できている PR を仕上げるときに選びます。コンフリクトの解消からマージまで機械が進めます。"
  },

  "empty": {
    "stats": "この期間に agent の工程の記録がありません。",
    "col": {
      "todo": "起票すると、ここに並びます。",
      "in_progress": "runner が回っているチケットが、ここに出ます。",
      "review": "PR を人間がレビューする段です。",
      "blocked": "人間の判断を待っているチケットが、ここに出ます。",
      "done": "完了したチケットは、ここに並びます。"
    },
    "tickets": "条件に合うチケットはありません。番号や題名を短くするか、PJ と状態を「すべて」にしてください。",
    "ticketRuns": "まだありません。上の「実行する」で作られます。",
    "ticketRunsNoProjectYml": "まだありません。project.yml を置くと実行できるようになります。",
    "ticketRunsBusy": "まだありません。動いているジョブが終わると、ここに出ます。",
    "ticketRunsDone": "ありません。やり直すには、上の「未着手に戻す（やり直す）」を押してください。",
    "runs": "まだありません。チケットの「実行する」か、ボードの「配車する」で作られます。",
    "jobs": "まだありません。起票・実行・配車・返却を押すと、ここに出ます。",
    "jobLog": "まだ出力がありません。数秒お待ちください。",
    "lent": "貸出中の VM はありません。",
    "keys": "まだ鍵が登録されていません。下の欄で登録すると、次に VM を借りる run から使われます。",
    "ls": "「一覧を取り直す」を押すと、Proxmox の VM 一覧をここに出します。",
    "lsVms": "プールの VM が 1 台もありませんでした。ジョブの記録で出力を確かめてください。",
    "log": "空です。",
    "logs": "条件に合う記録はありません。チケット番号を短くするか、PJ と種類を「すべて」にしてください。",
    "logFile": "まだありません。起票や配車をすると作られます。"
  },

  "help": {
    "statsSource": "数字の出どころは各工程の agent-<工程>-<n>.jsonl です。result の usage（入力・キャッシュ書込・キャッシュ読出・出力）と num_turns、system の init に書かれたモデル名を読みます。",
    "statsCache": "ターンごとに、それまでの文脈をキャッシュから読み直します。キャッシュ読出はターン数と文脈の長さの積で増えます。入力はキャッシュに乗らなかった分だけです。",
    "statsCost": "費用換算は claude CLI が API 料金で計算した total_cost_usd の合計です。サブスクの利用枠（5 時間・7 日）がどの重みで数えるかは、ここからは分かりません。",
    "statsThinking": "thinking は assistant の thinking ブロックの数です。Opus は本文が記録に出ず署名だけなので回数しか分かりません。Fable は要約の本文が見えるので、その回数と文字数も出します。thinking のトークンは出力に含まれていて、別には数えられません。出力トークンと「見える文字」の差が、隠れた thinking の目安です。",
    "statsScope": "code の工程（gates・pr など）はモデルを使わないので載せません。dry-run は既定で除きます。実行中の工程は result がまだ無いので 0 のまま出ます。",
    "noTodo": "未着手のチケットがありません。先に起票してください。",
    "noProjectYml": "{pj} に project.yml が無いため、runner は動かせません。$AIFACTORY_WORKSPACE/projects/{pj}/project.yml を書いてください。",
    "pjReady": "{pj} には project.yml があります。配車すると runner が動きます。",
    "pjNotReady": "{pj} に project.yml が無いため、起票はできますが、配車すると人間待ちになります。$AIFACTORY_WORKSPACE/projects/{pj}/project.yml を書いてください。",
    "runBusy": "このチケットのジョブが動いています。終わるのを待ってください:",
    "runDefault": "VM を 1 台貸し出し、工程を順に回します。終わると状態は自動で進みます。",
    "runDone": "完了したチケットです。実行するには、先に「未着手に戻す（やり直す）」を押してください。",
    "runInProgress": "実行中の扱いになっています。別の run が動いていないか確かめてから実行してください。",
    "runReview": "レビュー待ちです。もう一度回すなら、内容を確かめてから実行してください。",
    "runBlocked": "人間待ちです。メモに書いた原因を直してから実行してください。",
    "dryRun": "VM を触らず、依頼文と手順を組むだけです。状態は変わりません。",
    "moveUndo": "押すとすぐ変わります。トーストの「元に戻す」で前の状態に戻せます。",
    "syncTitle": "runs/<run>/state.json を読み直して、チケットの状態を合わせます。",
    "intake": "LLM が PJ・種別・題名・完了条件を整えてから起票します。20 秒ほどかかり、結果はジョブに出ます。",
    "newTicket": "LLM を使わず、この内容のまますぐに起票します。できたチケットの画面へ移ります。",
    "dispatchMoved": "起票しただけでは実行は始まりません。チケットの「実行する」か、ボードの「配車する」で runner が動きます。",
    "release": "返却すると VM は snapshot clean に巻き戻ります。runner は終了時に自分で返却します。",
    "lsAxes": "返却しても VM はすぐには止まらないので、貸出が 0 台でも起動中の VM が並びます。",
    "idleStopAxes": "返却から {h} 時間使われなかった VM は停止候補になります。候補が {k} 台を超えると、古いものから節電で止まり、次の貸出のときに自動で起動します（30〜60 秒ほど余分にかかります）。",
    "idleStop": "使われていないので節電で止まっています。次の貸出のときに自動で起動します。",
    "idleCandidate": "使われていない時間が既定を超えています。候補が足切りの台数を超えると、古いものから節電で止まります。",
    "lsFailed": "Proxmox に届かなかったか、ssh が切れました。ジョブの記録を見てから「一覧を取り直す」を押してください。",
    "pjPool": "定義は設定の台数、実体は Proxmox にある VM の台数です。実体が定義より少ないと、定義の数だけ同時に走らせても貸出のときに空きなしで止まります。",
    "pjPoolMore": "空きは実体から貸出を引いた数です。snapshot clean の無い VM は一覧からは分からないので、空きに数えたまま貸出で飛ばされることがあります。",
    "pjPoolYml": "project.yml が無い PJ は起票できますが、配車すると人間待ちになります。",
    "pjPoolKeys": "Claude の鍵は「鍵」画面のプールから選ばれます。プールに Fable 用と Opus・Sonnet 用の両方があれば、PJ 別や全体の設定ファイルの鍵は使われません。値は見ていません（有無だけ）。",
    "kindUnknown": "種別 {kind} に合う workflow がありません。使える種別を選んで保存してください。",
    "keysHow": "鍵ごとに、どのモデルに使うかをチェックで決めます。Fable は計画・設計・レビューの工程で、Opus・Sonnet は実装・調査の工程で使うモデルです。両方にチェックを付けた鍵は、どちらにも使われます。",
    "keys": "チケットを実行するとき、モデルごとに、有効でチェックの合う鍵の中から「最後に使ってから一番時間が経っている鍵」を選びます。使用量が特定の鍵に偏らないようにするためです。",
    "keysSameTask": "実行中のチケットの鍵が途中で変わることはありません。同じチケットを回し直すときも、前と同じ鍵を優先します。",
    "keysFallback": "合う鍵がここに 1 本もなければ、これまでどおり設定ファイルの鍵（sandbox token set で保存したもの）を使います。",
    "keysSecret": "トークンの値はこの画面にも記録にも出しません。見えるのは末尾 4 文字だけです。",
    "keysDisable": "「有効」を外すか削除すると、その鍵を使っている実行中の VM に別の鍵を入れ直します。動いている工程はそのまま終わり、次の工程から別の鍵に切り替わります。",
    "keysFile": "保存先:",
    "keysError": "鍵の一覧を読めませんでした。制御系の keys.json を確かめてください。",
    "shortcutsToggle": "この一覧を出す / 閉じる。",
    "shortcutsClose": "ダイアログを閉じる。",
    "draftLost": "下書きの {v} は今は選べません。既定に戻しました。",
    "attach": "1 ファイル 20 MiB・1 チケット合計 100 MiB までです。トークンや鍵は添付しないでください。",
    "attachNotDraft": "選んだファイルは下書きに残りません。画面を離れると選び直しになります。",
    "attachEmpty": "まだ添付はありません。"
  },

  "msg": {
    "moved": "チケット {id} を{to}にしました。",
    "undone": "チケット {id} を{to}に戻しました。",
    "saved": "チケット {id} を保存しました。",
    "synced": "チケット {id} の状態を実行記録に合わせました。",
    "syncUndone": "チケット {id} の状態とメモを{to}に戻しました。",
    "filed": "チケット {id} を起票しました。",
    "attached": "{n} 件を添付しました。",
    "detached": "添付 {name} を消しました。",
    "stopSent": "止める合図（SIGTERM）を送りました。終わるまで数秒かかることがあります。",
    "lsStarted": "VM の一覧を取得しています。終わると表が入れ替わります。",
    "draftRestored": "前回の下書きを復元しました。",
    "draftCleared": "下書きを捨てました。",
    "keyAdded": "鍵 {name} を登録しました。次に VM を借りる run から使われます。",
    "keySaved": "鍵 {name} を保存しました。",
    "keyRemoved": "鍵 {name} を削除しました。",
    "keyReinject": "実行中の VM {n} 台に別の鍵を入れ直すジョブを起こしました。"
  },

  "err": {
    "unreachable": "サーバーに届きません。console/bin/console が動いているか確かめてください。",
    "badJson": "サーバーの応答を読めませんでした。画面を読み直してください。",
    "noOverview": "サーバーから状態を取れませんでした。しばらくして画面を読み直してください。",
    "noBody": "本文ファイルが見つかりません。tickets/ の中を確かめてください。",
    "noRoute": "この画面はありません: {h}。左のメニューから選んでください。",
    "blockNeedsNote": "何を待っているかを書いてください。",
    "emptyRequest": "依頼文が空です。取り込む文章を入れてください。",
    "needTitle": "題名を入れてください。",
    "needKeyName": "鍵の名前を入れてください。",
    "needKeyToken": "トークンを入れてください。claude setup-token で表示される文字列です。",
    "needKeyFlag": "「Fable に使う」と「Opus・Sonnet・Haiku に使う」の少なくとも片方にチェックを付けてください。",
    "attachFailedAfterNew": "チケット {id} は起票できましたが、添付できませんでした。チケットの画面から添付し直せます。"
  },

  "dialog": {
    "typeToConfirm": "確認のため {v} と入力してください",
    "dispatch": {
      "title": "配車する",
      "body": "未着手のチケットを古い順に runner へ回します。VM を 1 台貸し出し、1 件に 60 分以上かかることがあります。",
      "loading": "次に回るチケットを確かめています。",
      "nextIs": "次に回るチケット",
      "nextNote": "project.yml が無い PJ のチケットは、配車すると人間待ちになります。",
      "none": "回せる未着手のチケットがありません。先に起票してください。"
    },
    "run": {
      "title": "チケット {id} を実行する",
      "body": "VM を 1 台貸し出し、工程を順に回して PR まで進めます。",
      "cost": "60 分以上かかることがあります。途中経過はジョブと実行記録で追えます。"
    },
    "block": {
      "title": "チケット {id} を人間待ちにする",
      "body": "何を待っているかを残します。ボードのカードにも出ます。"
    },
    "release": {
      "title": "チケット {task} の VM を返却する",
      "body": "VM {vm} を snapshot clean に巻き戻して返却します。中の変更は消えます。",
      "runWarning": "この VM では run {run} が動いています（工程 {step}）。返却すると run は止まり、途中の作業は失われます。",
      "noRun": "この VM で動いている run はありません。",
      "sharedWarning": "この VM はチケット {others} にも貸出中です。返却すると snapshot clean に巻き戻るので、そのチケットの作業も消えます。台帳からはチケット {task} の行だけが消え、{others} の行は巻き戻った VM を指したまま残ります。"
    },
    "sync": {
      "title": "チケット {id} の状態を実行記録に合わせる",
      "body": "実行記録 {run} の結果を読み直して、チケットの状態とメモを書き換えます。",
      "newer": "チケット {id} は、この run が終わった後の {at} に更新されています。実行すると、その更新は上書きされます。",
      "same": "前と後で変わるところはありません。"
    },
    "keyRemove": {
      "title": "鍵 {name} を削除する",
      "body": "この鍵を一覧から消します。トークンの値は取り出せないので、元に戻せません。",
      "inUse": "この鍵はチケット {tasks} の VM で使用中です。削除すると、その VM に別の鍵を入れ直します。動いている工程はそのまま終わり、次の工程から別の鍵に切り替わります。",
      "ok": "削除する"
    },
    "keyToken": {
      "title": "鍵 {name} のトークンを入れ替える",
      "body": "名前はそのままで、トークンだけを新しいものに入れ替えます。前の値には戻せません。実行中の VM は、次に鍵を入れ直すまで前の値のまま動きます。",
      "ok": "入れ替える"
    },
    "keyDisable": {
      "title": "鍵 {name} を使わないようにする",
      "body": "この鍵はチケット {tasks} の VM で使用中です。有効を外すと、その VM に別の鍵を入れ直します。動いている工程はそのまま終わり、次の工程から別の鍵に切り替わります。",
      "ok": "使わないようにする"
    },
    "detach": {
      "title": "添付 {name} を消す",
      "body": "この添付のファイルを消します。元に戻せません。",
      "ok": "添付を消す"
    },
    "stop": {
      "title": "ジョブを止める",
      "body": "プロセスグループに SIGTERM を送ります。kb run の途中なら、VM は貸出中のまま残ることがあります。",
      "after": "止めたあとは、この画面の「次にすること」から状態を合わせるか、sandbox で返却してください。"
    }
  },

  "next": {
    "intakeDone": "チケット {id} を起票しました。内容を確かめて、実行するか配車してください。",
    "intakeDoneNoId": "取り込みは終わりました。ボードで新しいチケットを確かめてください。",
    "intakeDry": "判定だけ見ました（起票していません）。内容で良ければ、起票へ戻って取り込んでください。",
    "intakeDoneNoAttach": "チケット {id} は起票できましたが、添付できませんでした。チケットの画面から添付し直せます。",
    "intakeFailed": "取り込みはできませんでした。出力の最後の行を確かめ、依頼文を直してもう一度取り込んでください。",
    "runDone": "run は終わりました。実行記録で工程と結果を確かめてください。",
    "runDry": "dry-run が終わりました。状態は変わっていません。組まれた依頼文は実行記録で読めます。",
    "runStopped": "run は途中で止まりました。チケットの状態を実行記録に合わせ、VM が貸出中のままなら sandbox で返却してください。",
    "runStoppedOld": "run は途中で止まりましたが、チケット {id} はその後「{status}」になっています。今どうなっているかはチケットで確かめてください。",
    "stateLine": "このジョブは {end} に終わりました。チケット {id} の今の状態は「{status}」です（{at} 更新）。",
    "dispatchDone": "配車は終わりました。回したチケットはボードと実行記録に出ています。",
    "dispatchFailed": "配車はできませんでした。出力を確かめてください。",
    "releaseDone": "VM を返却しました。貸出中の一覧から消えています。",
    "releaseFailed": "返却はできませんでした。出力を確かめ、sandbox ls で実勢を取り直してください。",
    "lsDone": "VM の一覧を取りました。sandbox の画面に出ています。",
    "lsFailed": "VM の一覧を取れませんでした。Proxmox への ssh が通るか確かめてください。"
  }
};
