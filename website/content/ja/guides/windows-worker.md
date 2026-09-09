# Windowsワーカーの導入と運用

初回導入は [ワンライナーで導入する](worker-install.md) を参照。

aifactoryのチケットを、専用Windows VMの一般ユーザーで実行できる。VM内のGo製サービスがHTTPSで操作を取得し、PowerShellでコマンドを実行する。依頼は既存のMCP `ticket_run` または `kb run` を使う。

## Macとの違い

| 項目 | Macワーカー | Windowsワーカー |
| --- | --- | --- |
| サービスの場所 | Macホスト | 専用Windows VMの中 |
| 実行環境 | Tartで毎回複製するmacOS VM | 常駐Windows VMの一般ユーザー |
| シェル | Bash | Windows PowerShell 5.1 |
| 準備・ゲート | provision.sh / gates.sh | provision.ps1 / gates.ps1 |
| 返却 | ゲストVMを停止・削除 | runの作業フォルダーと一時プロファイルを削除 |

Windowsの初期版は**OS全体を毎回初期化しない**。同じ管理主体の信頼できるプロジェクト向けの専用VMとして使う。サービスはLocalSystemで稼働するが、タスクは別の一般ユーザーとして起動する。サービスのトークン・ジャーナル・タスクアカウントのパスワードは、その一般ユーザーから読めないACLで保護する。タスクには必要なGitHub AppトークンとClaude認証情報だけを一時的に渡す。

## 1. 専用VMと通信を準備する

Windows 11 x64の専用VM、PowerShell 5.1、Git for Windows、GitHub CLI、Claude CLIを用意する。既存の個人用Windowsを実行先にしない。Proxmoxでの管理にはQEMU Guest Agentを使える。基準VMから複製する場合は、自動ログイン・個人アカウント・認証情報を引き継がない基準イメージを用意する。

ゲストのfirewallに任せず、Proxmox側で送受信を制限する。受信の新規接続、プライベート網、リンクローカル、tailnet、不要なIPv6を遮断し、例外はワーカーHTTPS受信口と公開DNS、必要な公開HTTPS通信に限定する。専用bridgeとNATを使う場合は設定を永続化する。

別のProxmoxホスト上の制御系に接続するときは、送信元を限定したTCPリレーでワーカーのHTTPS受信口に転送できる。TLSは制御系まで維持し、URLのホスト名と証明書SANを一致させる。consoleやProxmox管理APIへの接続は許可しない。`network_ready` はサービスからの自動firewall検査ではないため、導入時に管理者が到達可能・遮断対象の両方を実機で確認する。

## 2. 登録とサービス導入

[Pull workers](https://github.com/akkijp-oss/aifactory/blob/main/workers/README.md)の制御系手順でWindows用のworker IDとトークンを登録する。トークンとCA証明書はSSH/QGAなど信頼できる管理経路で配布する。インストーラーのHTTP配布ディレクトリに秘密情報を置かない。

ビルド端末にはGo 1.25以降が必要。Windows VMへのGo導入は不要。

```bash
cd workers
CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -trimpath -o /tmp/aifactory-worker.exe ./cmd/aifactory-worker
```

配置先は `C:\ProgramData\AIFactoryWorker`。先に `private` フォルダーをSYSTEMとAdministratorsだけが読めるACLで作り、そこへ `worker.token` と `server.crt` を置く。設定例:

```json
{
  "worker": "windows-worker-01",
  "url": "https://ctl.example.internal:8766",
  "token_file": "C:\\ProgramData\\AIFactoryWorker\\private\\worker.token",
  "ca_file": "C:\\ProgramData\\AIFactoryWorker\\private\\server.crt",
  "state_dir": "C:\\ProgramData\\AIFactoryWorker\\private\\state",
  "work_root": "C:\\ProgramData\\AIFactoryWorker\\work",
  "task_user": "aifactory-task",
  "task_password_file": "C:\\ProgramData\\AIFactoryWorker\\private\\task.password"
}
```

管理者PowerShellで[サービス導入スクリプト](https://github.com/akkijp-oss/aifactory/blob/main/workers/templates/install-windows-worker.ps1)を実行する。

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-windows-worker.ps1 -WorkerExe C:\Setup\aifactory-worker.exe -ConfigFile C:\Setup\config.json
Get-Service AIFactoryWorker
```

スクリプトは専用一般ユーザーを作り、ランダムなパスワードを保護フォルダーへ保存する。batch logonの権利、フォルダーACL、自動起動サービス、異常終了時の再起動を設定する。既存サービスや既存アカウントへの上書き導入は拒否する。バイナリ更新はサービス停止→置換→起動の順で行い、ジャーナルは残す。

Git、gh、ClaudeをマシンのPATHへ登録する。既定のGit Bashは `C:\Program Files\Git\bin\bash.exe`。タスクのHOME、USERPROFILE、APPDATA、LOCALAPPDATA、TEMPはrun配下に置く。画面操作には、別途[computer-useの導入](computer-use.md)とログイン済みのデスクトップが必要。

## 3. プロジェクトを登録して実行する

workspaceのプロジェクト定義に追加する。

```yaml
name: example-windows
repo: organization/repository
base_branch: main
backend: windows-pull
worker: windows-worker-01
app_dir: C:/ProgramData/AIFactoryWorker/work/app
gates: gates.ps1
```

`app_dir` はワーカーの `work_root` に `/app` を付けた値。実行時は `work_root/<lease>/app` に置き換える。成果物は同じlease内の `work/<task>` に保存する。プロジェクト用のGitHub AppとClaude認証設定も制御系に用意する。

任意の `provision.ps1` は認証注入・clone前に一般ユーザーで実行する。管理者権限が必要なツールは事前にVMへ導入する。`gates.ps1` はPowerShellスクリプトとして用意し、外部コマンド失敗時は `exit $LASTEXITCODE` などで非ゼロを返す。workflowのstep名 `gates.sh` は共通の識別子として残り、WindowsではPJの `.ps1` を呼び出す。`pr-create.sh` もWindows用Git/gh処理へ対応する。`merge-pr` は未対応。PR直前のbase取り込み（`sync-base`）はPOSIXシェル前提のためWindowsでは飛ばす（従来どおりbaseを取り込まずPRを作る）。

```powershell
$ErrorActionPreference='Stop'
git diff --check
if($LASTEXITCODE){exit $LASTEXITCODE}
'PASS diff-check'
```

MCP `ticket_new` → `ticket_run`、または `kb run <id>` で実行する。サービスがofflineまたは予約中ならdispatchは飛ばす。1ワーカーにつき同時1run。

## 停止・成果物・復旧

PowerShellと子孫プロセスをWindows Job Objectに収容する。タスク終了・キャンセル・timeoutで子孫も終了させ、終了確認できない場合は `uncertain` として予約を保持する。サービス異常終了時もJob Objectのハンドル解放で停止する。ジャーナルに開始済みの操作は再起動後に自動再実行しない。

成果物は作業フォルダー直下の通常ファイルだけを回収し、SHA-256を確認してから作業フォルダーを削除する。`runtime.env` は回収しない。入力350 KB、成果物合計4 MiB、ログ1操作16 MiBの制限はMacと共通。ディレクトリ・reparse point・合計4 MiBを超える分は回収せず飛ばし、その名前と理由を `state.json` の `artifacts_skipped` に残す（回収側はここで止まらない）。ただしjunction・symlinkなどreparse pointが作業フォルダーに残っている場合は、ワーカー側の削除が中断して予約を保持する。

`--keep` は回収後も作業フォルダーとleaseを保持する。`--resume` は同じleaseを所有し、cloneとticket準備が完了した場合のみ対応する。途中まで失敗した初期準備は自動再作成しない。管理者がVMのプロセスと状態を確認し、既存の `control show` / `resolve --confirmed-stopped` で復旧する。予約を解除するには成功した `guest-release` の操作IDが必要。VM自体は稼働を続ける。

サービス状態は `Get-Service AIFactoryWorker`、稼働・ログ・結果は制御系の `control list` / `show` とMCPのジョブ・run記録で確認する。再起動前から進行中だった操作を再実行するためにstateディレクトリを削除してはいけない。

## 実機で確認した範囲

2026-09-07、Proxmox上の専用Windows 11 Pro x64 VMで確認した。

- Windowsサービスの稼働、VM再起動後のログインなしの自動復帰、HTTPS heartbeat、一般ユーザー実行、日本語出力、非ゼロ終了コード。
- タスクからサービスのトークンへのアクセス拒否。
- 通常終了後・timeout・キュー経由キャンセル時の子孫プロセス停止。
- 永続ジャーナルの排他・再起動後の重複実行防止、TLS検証。
- MCPのresearchチケットから調査・要約、成果物SHA-256照合、workspace削除、lease解放。回収時の進捗出力混入を修正し、同じleaseでresumeした。
- PowerShellゲートの非ゼロ終了を失敗として保持。

Windows上での製品ビルド、PR公開、GUI、自動VM初期化はこの受入試験では確認していない。サービス起動・通信エラーはWindowsのApplicationイベントログの `AIFactoryWorker` ソースも参照する。

画面操作の追加手順は[Mac・Windowsの画面操作](computer-use.md)を参照。
