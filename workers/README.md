# Pull workers

Macから制御系へ仕事を取りに来る実行バックエンド。MacにはGo標準ライブラリのみでビルドした `aifactory-worker` を1本配布する。Python、Go、Homebrewはワーカーの実行には不要。macOS VMを実行する場合は別途Tartとゲストイメージが必要。

## 現在の範囲

初めて導入する場合は [Macワーカーの導入と運用](../docs/macos-worker.md) を参照。セットアップ順、チケットからの実行、失敗時の復旧、実機で確認した範囲をまとめている。

- 制御系: Python標準ライブラリのHTTPS受信サービスとSQLite操作キュー。既存consoleから独立して起動する。
- Mac: Go製ワーカー。HTTPSによる操作取得、heartbeat、逐次ログ送信、結果返送、永続ジャーナル。
- 操作: `probe`、`guest-exec`、`guest-prepare`、`guest-release`。VM名と基準イメージは管理者のローカル設定で固定する。
- 同時1操作。停止・切断・異常終了で結果が不明なら `uncertain` として再割当を止める。クラッシュ後に同じコマンドを自動再実行しない。
- `backend: macos-pull` のプロジェクトを通常の `ticket_run` / `kb run` から実行する。制御系がrun単位のleaseを保持し、step間も他のrunを割り込ませない。
- `base_vm` を設定すると、prepareで専用ゲストをcloneし、成果物の受領・SHA-256照合後にreleaseでゲストを停止・削除する。失敗時はleaseと記録を保持する。既存VMは引き取らない。
- 対応するcode stepは `gates.sh` と `pr-create.sh`。`merge-pr` は未対応として失敗させる。プロジェクトをまたぐ自動リソース調整やGUI配信は対象外。

設計: [macOS連携の検討](../docs/macos-worker-study.md)。制御系からMacへの接続開始、Macホスト上での任意コマンド実行、ホストのホーム共有は使わない。

## ビルドとテスト

```bash
cd workers
go test -race ./...
CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -trimpath -o /tmp/aifactory-worker ./cmd/aifactory-worker
# Intel Mac用は GOARCH=amd64
cd ..
python3 -m unittest discover -s workers/tests -v
```

Go 1.25以降をビルド端末だけに用意する。配布先で `aifactory-worker --version` による実行確認ができる。バイナリはリポジトリにコミットしない。

## 制御系

1テナントに1つのDBを配置する。運用データと秘密情報はworkspaceまたは設定ディレクトリへ置き、リポジトリへ置かない。

```bash
python3 workers/bin/control --db "$AIFACTORY_WORKSPACE/workers/queue.sqlite3" \
  enroll mac-worker --token-file "$HOME/.config/aifactory-workers/mac-worker.token"
python3 workers/bin/control --db "$AIFACTORY_WORKSPACE/workers/queue.sqlite3" \
  serve --host '<control-address>' --port 8766 \
  --cert "$HOME/.config/aifactory-workers/server.crt" \
  --key "$HOME/.config/aifactory-workers/server.key"
```

ワーカーごとのランダムトークンは指定ファイルへ0600で保存し、画面には表示しない。DBにはハッシュのみ保存する。console用の合言葉は使わない。`enroll` は制御系のローカル管理操作であり、ネットワークからの登録・任意操作投入APIは公開しない。登録コードによるセルフサービス登録は後続の実装。

HTTPSには接続先名をSANに持つ証明書を用意し、対応する信頼済み証明書をMacに配布する。公開CAでも専用CAでもよい。TLS検証を無効化するオプションはない。HTTPS以外のURLとリダイレクトも拒否する。

`templates/aifactory-workers.service` のプレースホルダーを実際の値へ置換してsystemdへ配置する。制御系の受信は8766/TCPを対象Macの経路に限定する。既存firewallの制御系→tailnetのNEW遮断は維持する。Macからgw経由で入る際のSNATは考慮し、ワーカーの所属はIPだけでなく個別トークンで識別する。

`sandbox/proxmox/50-firewall.sh` の `SB_WORKER_PORT`（既定0、無効）を8766に設定すると、制御系グループにgwからの受信を追加できる。gwでSNATしない構成はその送信元に合う限定ルールも必要。設定値は構築用の非公開envに永続化する。

## Macの設定

設定例（全て絶対パス。tokenとCA証明書は信頼できる管理経路で配布する）:

```json
{
  "worker": "mac-worker",
  "url": "https://ctl.example.internal:8766",
  "token_file": "/Users/operator/.config/aifactory-worker/worker.token",
  "ca_file": "/Users/operator/.config/aifactory-worker/server.crt",
  "state_dir": "/Users/operator/Library/Application Support/aifactory-worker",
  "tart": "/opt/homebrew/bin/tart",
  "guest_vm": "aifactory-macos-guest",
  "base_vm": "aifactory-macos-base"
}
```

`guest_vm` 未指定ならprobeだけを受け付ける。`base_vm` は必要なツールを入れて停止した、認証情報を含まないローカル基準VM。`guest_vm` には未使用の名前を指定する。基準VMにはTart Guest AgentとPython 3が必要。プロジェクトの `provision.sh` があれば、専用ゲスト内で認証情報を注入する前に実行する。gh、Claude CLI、GNU timeoutなどの導入はここで行える。Mac用のprovisionはツール導入に限定し、リポジトリのcloneはrunnerに任せる。既存のLinux用provisionをそのまま流用しない。イメージ取得は管理者が行う。個人利用のVMを指定しない。キャンセルやタイムアウトでは遠隔プロセスを残さないためゲスト全体を停止する。

```bash
~/.local/bin/aifactory-worker --config "$HOME/.config/aifactory-worker/config.json"
```

常駐は `templates/com.aifactory.worker.plist` を `~/Library/LaunchAgents/` に配置する。設定値はXMLとしてエスケープする。TartがSoftnetを見つけられるよう、テンプレートはHomebrewのbinをPATHに含める。plistの環境変数を変えた場合はbootout/bootstrapで再読み込みする。これはログインユーザーのLaunchAgentであり、再起動後ログイン前の稼働を保証するLaunchDaemonではない。VMのGUIセッションとサービス専用アカウントの運用は後続の検証対象。

Softnetは事前に管理者がroot所有・SUIDを設定する。このワーカーはPATHとこの設定を `network_ready` で報告し、未準備ならrunをVM割当前で待機させる（dispatchは飛ばす）。初期実装の準備判定はSUID方式を対象とする。HomebrewでSoftnetを更新した場合も設定を再確認する。

```bash
softnet_binary="$(/opt/homebrew/bin/brew --prefix softnet)/bin/softnet"
sudo chown root:wheel "$softnet_binary"
sudo chmod u+s "$softnet_binary"
```

## 操作と復旧

```bash
python3 workers/bin/control --db "$AIFACTORY_WORKSPACE/workers/queue.sqlite3" list
python3 workers/bin/control --db "$AIFACTORY_WORKSPACE/workers/queue.sqlite3" submit mac-worker probe
python3 workers/bin/control --db "$AIFACTORY_WORKSPACE/workers/queue.sqlite3" show '<operation-id>'
```

ゲスト操作は `{"command":"sw_vers; uname -m","timeout":30}` のようなJSONを非公開ファイルへ書き、`submit mac-worker guest-exec --payload-file <file>` で指定する。ホストVM名やホストシェルを操作のpayloadから選択することはできない。診断用payloadはDBに記録されるので、トークン・秘密情報は渡さない。

- `cancel <operation-id>`: 停止要求。通信が切れている間は停止完了にはしない。
- `disable <worker>`: そのワーカーの認証を失効。ゲストは通信断猶予の後に停止を試みる。失効と停止完了は別。
- `resolve <operation-id> --confirmed-stopped`: 管理者がゲスト停止・状態を確認した後だけ、uncertainによる予約を解除。以前の結果とログは保存する。
- 通信断猶予は60秒、コマンドtimeoutは1〜3600秒。停止確認できない場合はuncertain。ワーカーのオフライン判定は最後のheartbeatから45秒。
- ログは1操作16MiBまで。上限超過・永続化エラーは通常成功にしない。ジャーナルと制御系の操作履歴は自動削除しない。初期版には総容量の自動管理がないため、検証用の限定運用とする。
- ジャーナルを削除してから同じ未完了操作を再開しない。起動済みコマンドの再実行を防ぐ根拠になる。
- 制御系の完了応答が失われても同じ結果を再送できる。ログは操作IDと連番で重複排除し、全ログ受領前の完了は拒否する。

## チケットからMac VMで実行する

プロジェクト定義に以下を追加する。`app_dir` は専用ゲストのアカウントに合わせる。

```yaml
backend: macos-pull
worker: mac-worker-01
app_dir: /Users/admin/app
```

制御系は `AIFACTORY_WORKER_DB`（既定 `$AIFACTORY_WORKSPACE/workers/queue.sqlite3`）を使用する。`sandbox` のPJ別設定にGitHubリポジトリとClaude OAuthトークンを用意する。GitHub Appのトークンは対象リポジトリだけに払い出し、認証情報はHTTPSで運ぶ一時stdinファイルに格納する。操作DBと `show` に値を保存せず、受領完了後に一時ファイルを削除する。操作IDと入力のハッシュは記録に残る。コマンド自身で認証情報を出力しないこと。

`kb run <id> --dry-run` はVMを使わず、Macの作業パスを含む依頼文を確認する。本実行は `kb run <id>`。MCPの `ticket_run` も同じrunnerを呼ぶ。`dispatch` はMacがoffline、予約中、基準イメージ未準備、またはlifecycle非対応の場合にそのチケットを飛ばし、他のチケットへ進む。MCPから直接開始したrunは、onlineのMacの基準イメージ取得を最大6時間待機できる（`AIFACTORY_MAC_PREPARE_WAIT_S` で秒数を指定）。待機中はleaseもVMも割り当てず、`prepare.log` とジョブログに記録する。

ゲストは4 CPU・8 GiBで起動し、ホストディレクトリ・クリップボード・音声を共有しない。SoftnetでプライベートIPv4、リンクローカル、tailnet宛てを遮断し、ゲストDNSは公開DNSに設定する。基準VMのネットワークサービス名は `Ethernet` が前提。異なる構成はprepareが失敗するので、実機で検証してから使用する。

成果物はrunの作業ディレクトリ直下の通常ファイルに限定し、合計4 MiB、個別入力は350 KBまで。認証情報 `runtime.env` は回収しない。`runs/<run>/worker-operations.log` で操作IDを追い、`artifacts.json` に回収したファイルのハッシュ、`state.json` にbackend、worker、lease、回収・返却状態を記録する。転送失敗時にVMを削除しない。

`--keep` は回収後もVMとleaseを保持する。`--resume` は記録されたleaseを所有している場合だけ継続する。認証注入・リポジトリ作成前のprovision失敗は、同じ稼働中ゲストで再試行できるため、provision.shは再実行可能にする。途中まで作られたリポジトリや停止したゲストは自動で引き取らない。`kb run --resume` は日付が変わっていてもチケットに記録されたrunを使う。失敗後の再実行で新たなVMを自動割当しない。状態不明なら既存の `control show` / `resolve` で操作を確認し、専用ゲストの状態を確定してから復旧する。成功した `guest-release` 操作を指定した `control release-lease <worker> <lease> --operation <id>` だけが制御系の予約を解放する。
