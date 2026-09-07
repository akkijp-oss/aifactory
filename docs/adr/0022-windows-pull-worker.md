# ADR-0022: Windows VM内のサービスを共通pull workerにする

- 日付: 2026-09-07
- 状態: 採用
- 関連: [ADR-0018](0018-macos-pull-worker.md)、[ADR-0020](0020-macos-workflow-backend.md)

## 背景と判断

Mac専用の操作キューを複製せず、同じHTTPSプロトコル、SQLiteキュー、lease、秘密stdin、永続ジャーナルをWindowsでも使う。GoのOS別ファイルでロック・永続化・プロセス制御を分け、Windows Service Control Managerへ対応する。workflowには `windows-pull` を追加し、PowerShell・Git for Windows・gh・Claudeを呼ぶ。

サービスは専用VMのLocalSystem、タスクは別の一般ユーザー。タスクはサービスの認証情報とジャーナルを読めない。実行ヘルパーはJob Objectへの所属が完了するまでコマンドを受け取らず、親のクラッシュ時にも未所属タスクを開始しない。KILL_ON_JOB_CLOSEと終了後のActiveProcesses確認で子孫プロセスを扱う。[Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)と[Go Windows service package](https://pkg.go.dev/golang.org/x/sys/windows/svc)を利用する。

## 制約

初期版は専用Windows VMを常駐させ、返却時の初期化範囲はrunのworkspaceと一時プロファイル。MacのVM複製・削除と同等のOS初期化を保証しない。別の管理主体のプロジェクトを混在させず、ネットワーク遮断はProxmox側で設定・検証する。GUI操作、全VM snapshot rollback、Windows以外のシェル互換性、自動PRマージは対象外。

## 結果

チケット・工程・ログ・成果物の利用者向け操作を共通化できる。Windows固有のプロジェクト準備・ゲートは `.ps1` で提供する。失敗時はleaseを保持し、不明状態を自動で成功や再実行へ変換しない。導入手順は[Windowsワーカー](../windows-worker.md)に記録する。
