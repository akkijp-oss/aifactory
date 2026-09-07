# Mac・Windows・Linuxの画面操作

専用ワーカー環境で、スクリーンショット、クリック、マウス移動、日本語を含む文字入力、キー操作、スクロールを実行できる。既存のpullキューと排他的なleaseを使う。ワーカーの接続先やVM名を依頼ごとに変更する機能はない。

## チケットから使う

画面操作ツールを導入したワーカーを指定し、プロジェクトの `project.yml` に追加する。

```yaml
backend: windows-pull # Macでは macos-pull
worker: windows-worker-01
computer_use: true
```

`ticket_run` / `kb run` で起動する各エージェントへ、VM内の `computer` MCPツールを渡す。`linux-pull` でも有効にできる。`--strict-mcp-config` で、この実行用に生成したMCP設定を使う。VMに入れたClaude CLIがMCPに対応している必要がある。

依頼には、操作するアプリ、入力内容、確認する表示、完了条件を具体的に書く。例：

> メモ帳を開き「日本語入力テスト」と入力する。入力前後の画面を確認し、入力後のスクリーンショットと確認結果を成果物に残す。

MCPの画面取得は作業フォルダーの `screenshot-latest.png` を更新する。最後の1枚を通常の成果物としてSHA-256確認後に回収する。複数枚必要なら、エージェントが別名で保存する。全成果物の合計上限は4 MiB。画面には機密情報が写り得るため、成果物の閲覧者にも同じ情報が見えることに注意する。

## AIFactory MCPから直接使う

AIFactory MCPを再接続すると次の3ツールが使える。既存のstdioプロセスは、更新前のツール一覧を保持している場合がある。

1. `computer_open` に `worker` を渡す。ワーカーを予約してVMを準備し、`session` を返す。
2. `computer_action` に `session` と操作を渡す。最初に `action: screenshot` で画面を確認する。
3. 終了時に `computer_close` に `session` を渡す。正常な返却を確認して予約を解除する。

```json
{"session":"desktop-...","action":"screenshot"}
{"session":"desktop-...","action":"click","x":300,"y":200}
{"session":"desktop-...","action":"type","text":"日本語入力テスト"}
{"session":"desktop-...","action":"key","keys":["CTRL","A"]}
{"session":"desktop-...","action":"scroll","amount":-3}
```

画像はPNG、最大幅1024ピクセル。座標は返された画像の左上が原点で、実画面への倍率変換はツールが行う。Windowsは仮想画面全体、Macはメイン画面を対象とする。`move` も `x` / `y` を使う。クリックの `button` は `left` / `right`、`count` は1 / 2。`type` はUTF-8で8192バイトまで。Macはクリップボードへ文字列を書いて貼り付けるため、ゲストのクリップボードを置き換える。貼り付けを禁止する入力欄には使えない。`key` は最大4キーで、Windowsの `WIN`、Macの `CMD`、共通の `CTRL` / `ALT` / `SHIFT` / `ENTER` / `TAB` / `ESC` / 矢印 / 英数字などに対応する。スクロールは正が上、負が下、範囲は-20〜20（0を除く）。

直接操作の画像は制御系の `$AIFACTORY_WORKSPACE/computer/<session>/` に保存し、画像応答と保存先・ハッシュを返す。`actions.jsonl` は操作IDと種類を記録する。入力文字は監査行へ書かないが、操作結果や画像に現れる内容まで隠す機能ではない。保存容量の自動整理は行わない。

CLIも同じセッション管理を使う。

```bash
workers/bin/computer open mac-worker-01
printf '%s' '{"action":"screenshot"}' | workers/bin/computer action desktop-...
workers/bin/computer close desktop-...
```

## Windowsへの導入

[Windowsワーカー](windows-worker.md)を導入した専用VMに、ログイン済みでロックされていない一般ユーザーのデスクトップを用意する。サービスのSession 0とは別に、同じタスクユーザーのログオン時タスク `AIFactoryDesktop` を起動する。自動ログイン自体はこのインストーラーでは設定しない。

リポジトリの `workers/` でビルドする（クロスビルド時は `GOOS=windows GOARCH=amd64 CGO_ENABLED=0`）。

```powershell
go build -trimpath -o aifactory-computer.exe ./cmd/aifactory-computer
go build -trimpath -ldflags=-H=windowsgui -o aifactory-desktop.exe ./cmd/aifactory-computer
```

2つのexeと `workers/computer/windows.ps1` をVMの `C:\ProgramData\AIFactoryWorker\bin\` へ置き、管理者PowerShellで `workers/computer/install-windows.ps1` を実行する。独自Rootはインストーラーで指定できるが、現在のworkflow/MCPクライアントは上記の既定パスを使う。

デスクトップエージェントは `127.0.0.1:31191` のみで待ち受け、専用トークンで認証する。トークンはタスクユーザーに読み取りのみ許可し、ワーカーの接続トークンとは分離する。外部へGUI用ポートを公開しない。画面操作は通常ユーザーの権限で行い、ロック画面やUACの保護されたデスクトップは操作しない。

Windowsは永続VMで、返却時の初期化範囲は作業フォルダー。GUIアプリ、入力内容、ユーザー設定の初期化は保証しない。依頼の最後に自分で開いたアプリや入力を片付ける。OS全体の初期状態が必要な検証には別途VM復元の運用が必要。

## Macへの導入

[Macワーカー](macos-worker.md)の専用Tartゲストで使用する。macOS 14以降と、ゲスト内のログイン済みデスクトップが必要。MacのSDKがある環境でビルドする。

```bash
cd workers
CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -trimpath -o /tmp/aifactory-computer ./cmd/aifactory-computer
swiftc -parse-as-library -O computer/macos.swift -o /tmp/desktop-native
```

2つのバイナリをゲストへ転送し、ゲストのログインユーザーで `workers/computer/install-macos.sh <バイナリのディレクトリ>` を実行する。既定の配置先は `$HOME/.local/lib/aifactory-computer/`。workflowでは `app_dir` の親がそのHOMEであることを前提とする（例：`/Users/admin/app`）。Tart経由のstdin転送には `tart exec -i` を使う。

画面収録とアクセシビリティの許可を、ゲストのシステム設定またはOSの確認画面で与える。Tart経由では許可の対象が `tart-guest-agent` と表示される場合がある。実際に画面取得・クリック・文字入力を試し、ゲストを停止して新しい基準VMへcloneする。ワーカー設定の `base_vm` を新しい名前に切り替え、作り直したゲストでも再確認する。既存の基準VMは復旧用に残す。

検証環境のCirrus Labs製macOSイメージはSIP無効の状態だった。この機能の導入手順としてSIPの無効化やTCCデータベースの直接編集は行っていない。標準のSIP有効イメージでの同等動作は別途確認が必要。

## 失敗時

ログアウト、画面ロック、権限不足、デスクトップエージェント停止は正常な操作結果として扱わない。操作の応答が不明になった場合は同じ入力を自動再実行しない。セッションと操作履歴を調べ、ゲストの状態を確認してから返却する。ワーカーが他のチケットで使用中なら、新しい画面操作セッションを開始できない。

この機能は静止画を見ながら操作するもので、動画配信、音声操作、ドラッグ操作には対応していない。

## 実機確認

2026-09-07に専用Windows VMで、AIFactory MCPによるセッション作成・クリック・日本語入力・画面取得・返却を確認した。さらに `computer_use: true` の調査チケットで、エージェントがメモ帳へ31文字を入力し、別の判定ステップが回収前のPNGを読んで一致を確認した。最新PNG（約794 KiB）、調査結果、要約をハッシュ確認後に回収し、leaseを解放した。

スクロール操作はエラーなく実行できたが、Windows試験は1行だけの書類だったため、内容が実際にスクロールする場面は未確認。日本語の `type` はUnicode入力で、IMEの変換候補を選ぶ試験ではない。

Macでは、設定を保存した基準VMから新しいゲストを起動し、AIFactory MCP経由でTextEditへのクリック・日本語入力・画面取得を再確認した。許可の追加操作は不要だった。

同じMacの調査チケットでも、日本語入力のPNG、調査報告、判定結果を回収して予約を解除した。この試験で長文の合成キー入力に文字欠落を見つけたため、貼り付け方式へ修正した。修正後はAIFactory MCPから改行・日本語・絵文字を含む41行・942文字を入力し、TextEditで全選択してコピーした全文との完全一致を確認した（比較前にクリップボードを空にして、入力用データの読み戻しと区別）。

Linuxも同じMCPを利用できる。[単体Linuxの導入と制限](linux-worker.md)を参照。
