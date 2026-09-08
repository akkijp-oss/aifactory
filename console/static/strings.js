/* aifactory console の文言。画面に出る日本語はここに集める（console/UX.md）。
   本体は厳密な JSON（tests/test_strings.py が読み、表記のルールと app.js / index.html との鍵の対応を検査する）。
   - btn.* とダイアログの ok: ユーザーの行為を動詞で（「実行する」）。「OK」「はい」は使わない。取り消しは「キャンセル」
   - msg / err / empty / help / sub / next / banner とダイアログの本文: 「ですます」の文。末尾は「。」
   - 用語は用語集どおり（チケット / PJ / 実行記録 / 工程 / ジョブ / 貸出）。「タスク」「プロジェクト」は使わない */
const T = {
  "status": { "todo": "未着手", "in_progress": "実行中", "review": "レビュー待ち", "blocked": "人間待ち", "done": "完了" },
  "jobState": { "running": "実行中", "done": "終了", "failed": "失敗", "stopped": "止めた", "lost": "記録なし", "ended": "終了（終了コード不明）" },
  "result": { "end": "終了", "human": "人間へ", "failed": "失敗（開始前）" },
  "time": { "sec": "{n} 秒", "min": "{n} 分", "hourMin": "{h} 時間 {m} 分" },
  "nav": { "board": "ボード", "tickets": "チケットの一覧", "intake": "起票", "runs": "実行記録", "jobs": "ジョブ", "sandbox": "sandbox", "logs": "ログ", "config": "設定",
           "updated": "更新 {t}", "shortcuts": "? でショートカット" },
  "conn": { "on": "接続中", "off": "切断" },
  "banner": { "offline": "サーバーに届きません。console/bin/console が動いているか確かめてください。" },

  "btn": {
    "cancel": "キャンセル", "close": "閉じる", "undo": "元に戻す", "draftClear": "下書きを捨てる",
    "file": "起票する", "dispatch": "配車する", "run": "実行する", "dryRun": "dry-run で依頼文だけ確かめる",
    "save": "保存する", "sync": "実行記録に状態を合わせる", "intake": "取り込む",
    "refreshVms": "一覧を取り直す", "release": "返却する", "stop": "止める",
    "start": "開始にする", "review": "レビュー待ちにする", "done": "完了にする", "reopen": "未着手に戻す", "redo": "未着手に戻す（やり直す）", "block": "人間待ちにする",
    "openReason": "理由を読む", "openReport": "報告を読む", "openLatestRun": "最新の実行記録を開く",
    "openTickets": "一覧で探す", "openTicket": "チケットを開く", "openRun": "実行記録を開く", "openRuns": "実行記録の一覧を見る", "openSandbox": "sandbox を見る", "openBoard": "ボードへ戻る", "openIntake": "起票へ戻る", "openJob": "ジョブを開く"
  },

  "h": {
    "outcome": "結果", "artifacts": "成果物", "stepLogs": "工程のログ", "otherFiles": "その他のファイル（{n} 件）",
    "run": "runner で回す", "move": "状態を進める", "fix": "項目を直す", "runs": "実行記録", "jobs": "このコンソールのジョブ", "body": "本文", "history": "履歴",
    "track": "工程", "files": "ファイル", "lent": "貸出中", "pjPool": "PJ とプール", "lsResult": "sandbox ls の結果",
    "intakeFree": "自由文から起票する", "intakeNew": "整った本文で起票する", "next": "次にすること", "output": "出力",
    "routes": "モデルの経路", "thisConsole": "この console", "shortcuts": "キーボードの近道"
  },

  "th": {
    "run": "実行記録", "ticket": "チケット", "workflow": "workflow", "started": "開始", "elapsed": "所要", "result": "結果", "step": "工程",
    "at": "日時", "field": "項目", "before": "前", "after": "後", "state": "状態", "what": "内容", "rc": "終了コード",
    "title": "題名", "updated": "更新", "lentSince": "貸出から", "token": "トークン", "lent": "貸出", "command": "コマンド", "pidRc": "プロセス ID / 終了コード", "name": "名前", "flow": "流れ"
  },

  "label": {
    "q": "番号・題名", "qPlaceholder": "204 や 起票 のように", "status": "状態", "allStatus": "すべて",
    "pj": "PJ", "allPj": "すべて", "pjIfKnown": "PJ（分かっていれば）", "letLlm": "LLM に決めさせる", "kind": "種別", "workflow": "workflow", "option": "オプション",
    "workflowAsKind": "{kind}（種別のまま）", "keep": "終了後も VM を返却しない（中を見る）", "resume": "貸出中の VM で続きから（--resume）",
    "pr": "PR 番号", "prForMerge": "PR 番号（merge-pr のとき）", "note": "メモ", "notePlaceholder": "何を待っているか / 何をしたか",
    "blockNote": "何を待っていますか", "blockPlaceholder": "例: 本番 DB の権限を管理者に依頼中",
    "count": "件数", "count1": "1 件だけ", "count3": "3 件まで", "count10": "10 件まで", "countAll": "未着手が尽きるまで",
    "dispatchDry": "dry-run（VM を触らず、状態も進めません）", "intakeDry": "起票せず、判定だけ見る",
    "request": "依頼文（音声の書き起こし、チャットの貼り付け、箇条書き、何でも）", "requestPlaceholder": "例: seeds が今のモデルに合っていなくて db:seed が落ちる。直してほしい",
    "title": "題名（1 行目になり、ブランチ名と PR の題名に使います）", "titlePlaceholder": "fix: … / docs: … / feat: …", "body": "本文（Markdown。末尾に「## 完了条件」）",
    "runsAll": "dry-run と退避分（-attemptN）も見る", "fetching": "取得中"
  },

  "sub": {
    "board": "未着手 → 実行中 → レビュー待ち → 完了。人間待ちは横に置きます。",
    "tickets": "番号・題名・PJ・状態で探せます。完了したチケットもすべてここに並びます。",
    "runs": "runs/ の state.json を読んでいます。",
    "sandbox": "貸出状況は state.json、VM の実勢は sandbox ls（Proxmox に ssh、数秒）で取ります。",
    "intake": "自由文は intake（LLM 1 回）、整った本文は kb new で起票します。",
    "jobs": "このコンソールが起動した CLI です。記録は console/jobs/ に残ります。",
    "logs": "起票（intake）と配車（dispatch）のログです。工程ごとの記録は実行記録、状態の履歴はチケットで見られます。",
    "config": "読むだけの画面です。変えるときはファイルを編集してください。"
  },

  "board": {
    "liveStep": "{step} を実行中 {t}", "liveNext": "次は {step}", "liveSince": "（開始から {t}）", "jobsRunning": "ジョブ {n} 件が実行中",
    "runsCount": "実行記録 {n} 件（うち開始前 {m} 件）", "ticketCount": "左の数字はチケットの件数です。",
    "noLive": "動いている run はありません。", "more": "ほか {n} 件をすべて見る",
    "scopeAll": "集計と列の対象: すべての PJ", "scopePj": "集計と列の対象: PJ {pj}"
  },
  "tickets": { "count": "{n} 件（全 {m} 件）" },
  "ticket": { "crumb": "チケット {id}", "dbRun": "台帳に記録された run:", "stamps": "作成 {c} / 更新 {u}" },
  "intake": { "pjReadyBadge": "実行できます", "pjNotReadyBadge": "準備が必要", "checkSandbox": "sandbox で準備状態を見る" },
  "run": {
    "nextStep": "次は {step}", "elapsed": "{t} 経過", "plan": "定義:", "wip": "退避", "loops": "戻し", "v0": "v0 の記録（Markdown 1 枚）です。",
    "truncated": "末尾 300 KB だけ表示しています。", "following": "{step}（{kind}）の出力を追い読みしています。", "refresh": "5 秒ごとに更新します。",
    "notStarted": "開始前（記録なし）", "error": "失敗の理由",
    "noState": "state.json がありません。工程が始まる前に止まった run です。今の状態はチケットで確かめてください。"
  },
  "outcome": {
    "pr_created": "PR ができました。次はレビューです。",
    "loop_limit": "工程 {step} が {n} 回続けて通らず、人間待ちになりました。",
    "step_failed": "工程 {step} で止まりました。",
    "gateFails": "赤いゲート: {gates}。",
    "ended": "すべての工程が終わりました。",
    "waiting": "工程は終わり、人間の判断を待っています。",
    "running": "工程 {step} を実行中です。",
    "unknown": "止まった理由は記録にありません。工程のログを確かめてください。",
    "noDetail": "理由を書いたファイルは残っていません。",
    "ticketNow": "この run は {end} に終わりました。チケット {id} の今の状態は「{status}」です（{at} 更新）。",
    "ticketNewerRun": "チケット {id} の最新の実行記録は {run} です。",
    "noTicket": "この run に対応するチケットはありません。"
  },
  "artifact": {
    "ticket": "チケット", "researcher": "調査", "planner": "計画", "implementer": "報告", "reviewer": "レビュー", "judge": "まとめ", "gates": "検証結果"
  },
  "sandbox": {
    "count": "{n} 台", "perPj": "PJ あたり {n} 台", "runOn": "run が動いています（工程 {step}）", "yes": "あり", "no": "なし",
    "tokenSaved": "保存済み", "tokenMissing": "未設定", "lsAt": "{t} 取得", "lsNever": "まだ取っていません"
  },
  "job": { "following": "2 秒ごとに追い読みしています。" },
  "config": { "roles": "役割:" },

  "empty": {
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
    "ls": "「一覧を取り直す」を押すと、Proxmox の VM 一覧をここに出します。",
    "log": "空です。",
    "logFile": "まだありません。起票や配車をすると作られます。"
  },

  "help": {
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
    "intake": "Mac 側で claude -p を 1 回呼びます（20 秒ほど）。結果はジョブに出ます。",
    "newTicket": "すぐに起票します。できたチケットの画面へ移ります。",
    "dispatchMoved": "配車（未着手を runner に回す）はボードの「配車する」から行います。",
    "release": "返却すると VM は snapshot clean に巻き戻ります。runner は終了時に自分で返却します。",
    "pjPool": "project.yml が無い PJ は起票できますが、配車すると人間待ちになります。トークンはファイルの有無だけを見ています（中身は表示しません）。",
    "kindUnknown": "種別 {kind} に合う workflow がありません。使える種別を選んで保存してください。",
    "shortcutsToggle": "この一覧を出す / 閉じる。",
    "shortcutsClose": "ダイアログを閉じる。",
    "draftLost": "下書きの {v} は今は選べません。既定に戻しました。"
  },

  "msg": {
    "moved": "チケット {id} を{to}にしました。",
    "undone": "チケット {id} を{to}に戻しました。",
    "saved": "チケット {id} を保存しました。",
    "synced": "チケット {id} の状態を実行記録に合わせました。",
    "syncUndone": "チケット {id} の状態とメモを{to}に戻しました。",
    "filed": "チケット {id} を起票しました。",
    "stopSent": "止める合図（SIGTERM）を送りました。終わるまで数秒かかることがあります。",
    "lsStarted": "VM の一覧を取得しています。終わると表が入れ替わります。",
    "draftRestored": "前回の下書きを復元しました。",
    "draftCleared": "下書きを捨てました。"
  },

  "err": {
    "unreachable": "サーバーに届きません。console/bin/console が動いているか確かめてください。",
    "badJson": "サーバーの応答を読めませんでした。画面を読み直してください。",
    "noOverview": "サーバーから状態を取れませんでした。しばらくして画面を読み直してください。",
    "noBody": "本文ファイルが見つかりません。tickets/ の中を確かめてください。",
    "noRoute": "この画面はありません: {h}。左のメニューから選んでください。",
    "blockNeedsNote": "何を待っているかを書いてください。",
    "emptyRequest": "依頼文が空です。取り込む文章を入れてください。",
    "needTitle": "題名を入れてください。"
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
      "noRun": "この VM で動いている run はありません。"
    },
    "sync": {
      "title": "チケット {id} の状態を実行記録に合わせる",
      "body": "実行記録 {run} の結果を読み直して、チケットの状態とメモを書き換えます。",
      "newer": "チケット {id} は、この run が終わった後の {at} に更新されています。実行すると、その更新は上書きされます。",
      "same": "前と後で変わるところはありません。"
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
