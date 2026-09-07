# ワンライナーでワーカーを導入する

専用Macホスト、専用Windows、専用Linuxに公開スクリプトからワーカーとcomputer-use環境を導入する。所属先は `AIFACTORY_URL`、ワーカーIDと専用トークンは `AIFACTORY_WORKER` / `AIFACTORY_TOKEN` で渡す。Proxmoxは不要。ネットワークの接続制限は導入先で事前に設定する。

配布ブランチは `install/worker-bootstrap`。スクリプトと同じブランチのソースを取得し、公式Go配布サイトのSHA-256で検証したツールチェーンでビルドする。Goの事前導入やリリース用バイナリの手作業配置は不要。初回はソース・依存ツール・Mac VMイメージのダウンロードに時間がかかる。

## 制御系で一度準備する

このブランチの制御系（`POST /v1/check` 対応）を起動し、ワーカーごとに登録する。URLはconsoleではなくワーカー用HTTPS受信口を指定する。

```bash
python3 workers/bin/control --db "$AIFACTORY_WORKSPACE/workers/queue.sqlite3" enroll linux-worker-01 --token-file "$HOME/.config/aifactory-workers/linux-worker.token"
```

生成された専用トークンを導入先へ安全に渡す。ワーカーごとに別のID・トークンを使う。GitHub App / Claudeの認証情報、PJの登録は制御系に置き、インストーラーへ渡さない。

以下の `XXXXXX` はそのトークンへ置き換える。コマンド履歴に残したくない場合は、事前に秘密の入力方法で環境変数を設定し、例の値を既存変数の参照へ置き換える。

## Linux

Ubuntu 24.04 / Debian 13以降など、aptとsystemd 254以降を備えたamd64 / arm64の専用インスタンスで実行する。

```bash
curl -fsSL https://raw.githubusercontent.com/akkijp-oss/aifactory/refs/heads/install/worker-bootstrap/workers/install.sh | sudo env AIFACTORY_URL='https://ctl.example.com:8766' AIFACTORY_WORKER='linux-worker-01' AIFACTORY_TOKEN='XXXXXX' bash
```

一般作業ユーザー、Git・gh・Claude CLI、Xvfb/Openbox・ランチャー・日本語フォント・Mousepad、ワーカーとデスクトップのsystemdサービスを導入する。`Alt+F2` でアプリを起動できる。再起動後も自動起動する。ネイティブWaylandは対象外。

## Mac

Apple Silicon Macのログイン中ユーザーで実行する。HomebrewとApple Command Line Toolsは事前に用意する。Mac全体を `sudo bash` で実行しない。

```bash
curl -fsSL https://raw.githubusercontent.com/akkijp-oss/aifactory/refs/heads/install/worker-bootstrap/workers/install.sh | AIFACTORY_URL='https://ctl.example.com:8766' AIFACTORY_WORKER='mac-worker-01' AIFACTORY_TOKEN='XXXXXX' bash
```

Tart / Softnet、専用基準VM、ゲスト内のCLIと画面操作ヘルパー、ホストのLaunchAgentを準備する。Softnetの設定ではsudo認証が必要な場合がある。新規基準VMの既定イメージは `ghcr.io/cirruslabs/macos-sequoia-base:latest`。既存の専用基準VMは `AIFACTORY_MAC_BASE` で指定できる。

**初回の画面収録・アクセシビリティ許可はmacOSの画面で承認する。** スクリプトは権限確認が通る前にワーカーの導入完了を報告しない。許可が必要なら表示された基準VMをTartで開き、Tart Guest Agent / desktop-nativeに許可を与え、VMを停止して同じコマンドを再実行する。TCCデータベースは書き換えない。LaunchAgentの稼働にはホストへのログインが必要。

## Windows

Windows 11 x64の専用マシンで、管理者PowerShellを開いて実行する。

```powershell
$env:AIFACTORY_URL='https://ctl.example.com:8766'; $env:AIFACTORY_WORKER='windows-worker-01'; $env:AIFACTORY_TOKEN='XXXXXX'; $env:AIFACTORY_AUTOLOGIN='1'; irm https://raw.githubusercontent.com/akkijp-oss/aifactory/refs/heads/install/worker-bootstrap/workers/install.ps1 | iex
```

Git・gh・Claude CLI、一般作業ユーザー、Windowsサービス、画面用のログオンタスクを準備する。`AIFACTORY_AUTOLOGIN=1` は専用作業ユーザーの自動ログインを設定する。生成したパスワードは保護されたファイルとLSA secretへ保存し、コマンド引数やWinlogonの平文 `DefaultPassword` へ書かない。自動ログインは次の再起動から有効になり、インストーラーは勝手に再起動しない。

自動ログインを希望しない場合は `AIFACTORY_AUTOLOGIN` を省略する。computer-useには専用作業ユーザーのログインとロックされていない画面が必要。既存環境で変数を省略した場合、既存の自動ログイン設定を解除しない。

## 環境変数

| 変数 | 内容 |
| --- | --- |
| `AIFACTORY_URL` | 必須。ワーカー受信口のHTTPS origin。資格情報・パス・クエリは含めない |
| `AIFACTORY_WORKER` | 必須。制御系で登録したワーカーID |
| `AIFACTORY_TOKEN` | 必須。そのワーカー専用のトークン |
| `AIFACTORY_CA_B64` | 独自CA証明書のPEMをBase64にした値。省略時はシステムの信頼済みCAを使用 |
| `AIFACTORY_CA_FILE` | 導入先に既にあるCAのPEMファイル。`CA_B64` と併用しない |
| `AIFACTORY_SERVER_IP` | 任意。URLのホスト名をこのIPへhosts登録。異なる既存エントリーがあれば停止 |
| `AIFACTORY_REF` | 取得するGit ref。既定 `install/worker-bootstrap`。版を固定する場合はコミットSHAを指定し、入口URLにも同じSHAを使う |
| `AIFACTORY_MAC_IMAGE` | 新規Mac基準VMの取得元。再現性が必要ならdigestで固定 |
| `AIFACTORY_MAC_BASE` | Macの専用基準VM名。既存インストールでは現在の設定を引き継ぐ |
| `AIFACTORY_MAC_GUEST` | Macの実行用ゲスト名。既定 `aifactory-macos-guest` |
| `AIFACTORY_AUTOLOGIN` | Windowsのみ。`1` で専用ユーザーの自動ログインを設定 |
| `AIFACTORY_CHECK_ONLY` | `1` で設定形式とCAだけを検査。サービス・依存ツールは変更しない。接続確認ではない |

独自CAのHTTPSを使う場合もTLS検証を無効にしない。例に `AIFACTORY_CA_B64='PEMのBase64'` を追加する。トークンをGitHubのURL、公開ファイル、PJ定義へ入れない。

## 再実行と確認

同じID・接続先なら、同じコマンドで再ビルド・更新できる。別のID・接続先への上書きと、予約中のワーカー更新は拒否する。既存のジャーナル、トークン以外の認証情報、作業ユーザーのパスワードを初期化しない。途中で失敗した場合はエラーを解消して再実行し、予約やジャーナルを消して復旧しない。

インストーラーはTLS・ワーカー認証を実際に確認してから設定を更新する。通信確認は操作の取得や予約変更を行わない。制御系の `control list` で `online: true` と予約が空であることを確認し、MCPの `computer_open` → `computer_action` → `computer_close` で試す。

詳細は各OSの運用ガイドと [computer-use](computer-use.md) を参照する。
