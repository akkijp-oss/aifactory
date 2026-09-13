# 単体Linuxワーカー

初回導入は [ワンライナーで導入する](worker-install.md) を参照。

Linuxインスタンスへワーカーサービスを直接導入し、チケットのCLI処理とcomputer-useを実行できる。物理マシン、VPS、任意の仮想基盤で動くLinuxを対象とし、ProxmoxのAPI、VM ID、clone、snapshot操作は使わない。

## 構成

- rootの `aifactory-worker.service` が既存のHTTPS pullキューへ接続する。
- チケットのコマンドは、専用の一般ユーザー `aifactory-task` とsystemdの一時サービスで実行する。操作ごとにcgroupを分け、通常終了・timeout・キャンセル時にサービスの停止を確認する。
- 画面操作は別の `aifactory-desktop.service` が一般ユーザーとして担当する。X11画面内の操作に限定し、専用トークンで認証した `127.0.0.1:31191` へ接続する。
- 返却は作業フォルダーの削除。インスタンスの再作成、OS設定やGUIアプリの初期化は行わない。

初期対応はsystemd 254以降のLinux（Ubuntu 24.04以降など）とX11。Waylandのネイティブ画面操作は未対応で、Waylandセッションを検出した場合はエラーにする。GUIのないサーバーでは `--headless` によりXvfbとOpenboxで専用画面を用意できる。X11のTCP待受は無効にし、認証cookieを使う。画面操作にRDPやVNCの外部公開は不要。

## 導入

Ubuntu/Debianでの依存パッケージ例：

```bash
sudo apt-get update
sudo apt-get install python3 python3-pil xvfb xauth x11-utils xdotool xclip openbox gmrun dbus
```

チケットを実行する場合は、Git、GitHub CLI、GNU timeout、Claude CLIも導入し、一般ユーザーが使える `/usr/local/bin` または `/usr/bin` に配置する。provision.shは一般ユーザーとして実行されるため、OSパッケージのインストールは管理者が先に行う。

Go 1.25以降のビルド端末で、`workers/` から対象CPU用の2つのバイナリを作る。x86-64なら `GOARCH=amd64`、ARM64なら `arm64`。

```bash
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -o /tmp/linux-bin/aifactory-worker ./cmd/aifactory-worker
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -o /tmp/linux-bin/aifactory-computer ./cmd/aifactory-computer
```

制御系でワーカーを登録する。既存ワーカーのトークンを流用しない。

```bash
python3 workers/bin/control --db "$AIFACTORY_WORKSPACE/workers/queue.sqlite3" \
  enroll linux-worker-01 --token-file "$HOME/.config/aifactory-workers/linux-worker.token"
```

導入先のroot所有 `/etc/aifactory-worker/` に、専用トークンを `worker.token`、制御系のCA証明書を `server.crt` として転送する。ディレクトリは0700、ファイルは0600。トークンをコマンド行やチケットへ記載しない。

管理者用の設定ファイルを用意する。インストーラーは以下の固定パスと専用ユーザーを使う。

```json
{
  "worker": "linux-worker-01",
  "url": "https://control.example:8766",
  "token_file": "/etc/aifactory-worker/worker.token",
  "ca_file": "/etc/aifactory-worker/server.crt",
  "state_dir": "/var/lib/aifactory-worker/private/state",
  "work_root": "/var/lib/aifactory-worker/work",
  "task_user": "aifactory-task"
}
```

リポジトリの `workers/templates/` と `workers/computer/` を相対配置を維持して導入先へ転送する。2つのバイナリも転送し、rootで実行する。

```bash
sudo bash workers/templates/install-linux-worker.sh /root/linux-worker.json /root/linux-bin --headless
sudo systemctl status aifactory-worker aifactory-desktop
```

既存の専用X11ログイン画面を操作する場合は `--headless` を付けずに導入する。`workers/computer/aifactory-desktop-linux.service` をタスクユーザーの `~/.config/systemd/user/aifactory-desktop.service` へ置き、そのX11セッションのターミナルで次を実行する。仮想画面と既存画面のエージェントは同時起動しない。

```bash
systemctl --user import-environment DISPLAY XAUTHORITY XDG_SESSION_TYPE WAYLAND_DISPLAY
systemctl --user daemon-reload
systemctl --user enable --now aifactory-desktop.service
```

既存デスクトップ方式は専用の `aifactory-task` でX11ログインする運用を別途用意する。一般ユーザーをsudo、docker、lxdなどの管理用グループへ追加しない。

## 使い方

プロジェクトの `project.yml`：

```yaml
name: linux-app
repo: owner/repository
base_branch: main
backend: linux-pull
worker: linux-worker-01
app_dir: /var/lib/aifactory-worker/work/app
gates: gates.sh
computer_use: true
```

`app_dir` は `<worker work_root>/app` を指定する。実行時に `<work_root>/<lease>/app`、成果物は `<work_root>/<lease>/work/<ticket>` へ分離する。チケットは既存の `ticket_run` / `kb run` で実行できる。GUIを使わないプロジェクトでは `computer_use` を省略する。

鍵の出どころ: **Claude の鍵は制御系の鍵プール（`~/.config/sandbox/keys.json`）が正本**で、runnerが工程ごとに`sandbox keys pick` を呼び、モデル系統ごとに選んだ鍵をゲストの `runtime.env` に渡す（ADR-0044 / ADR-0046）。鍵を無効化すると次の工程から別の鍵に変わる。プールに合う鍵が無ければ、`pj/<pj>.env` や `env` の鍵にも**runnerのプロセスに残っている値にも落ちず**、runを「鍵なし」で一時停止して鍵の登録を待つ（ADR-0060）。consoleとMCPが起動するジョブの鍵も `~/.config/aifactory/ctl.env` が正本で、ジョブごとに読み直す。

AIFactory MCPからの直接操作も共通の `computer_open`、`computer_action`、`computer_close` を使う。`computer_open` の `worker` にLinuxワーカー名を渡す。[操作の引数と画像の回収](computer-use.md)を参照。

画面は最大幅1024ピクセルで返し、クリック座標を実画面へ換算する。Linuxの `type` はクリップボードへUTF-8文字列を書き、Ctrl+Vで貼り付ける。貼り付け禁止欄や、異なる貼り付けキーを使うアプリにはそのまま使えない。改行・日本語を含む入力も、画面を取得して確認する。

GUIアプリのホームは `/home/aifactory-task`、チケット用シェルの `HOME` は予約ごとの作業ディレクトリになる。同じファイルシステムを使うが、`~/file` は別の保存先になる。回収したいファイルは、依頼文にある成果物ディレクトリの絶対パスをGUIの保存ダイアログへ指定する。

ヘッドレス構成では `Alt+F2` で `gmrun` を開き、インストール済みアプリの名前を入力して起動できる。`Alt+Tab` でウィンドウを切り替え、`Alt+F4` で閉じる。日本語を表示するアプリには対応フォントも導入する。

## 運用上の範囲

専用インスタンスを実行境界にする。ワーカーの接続トークンとジャーナルはroot専用、画面用トークンだけをタスクユーザーへ読み取り許可する。作業フォルダーを削除しても、デスクトップ、クリップボード、タスクユーザーのホーム、GUI経由で起動したアプリの状態は残る。ログアウトや画面ロック中は使わず、ヘッドレス方式ではロック機能を追加しない。

ネットワークはそのLinuxインスタンスの設定に従う。このワーカーはProxmox/Softnetのネットワーク分離を適用しない。必要な接続制限は導入先のファイアウォールで設定する。

操作の停止が確認できない場合や、ワーカー再起動時に未完了のジャーナルがある場合は `uncertain` として予約を保持する。同じ入力を自動再実行しない。`systemctl`、`journalctl`、制御系の操作履歴を確認し、既存の復旧手順で扱う。画面用サービスの停止は `systemctl stop aifactory-desktop`、ワーカーの停止は `systemctl stop aifactory-worker`。

## 検証範囲

Ubuntu 24.04 ARM64の専用テスト環境（Docker上、systemd PID 1）で検証した。Proxmoxを使わずにワーカーとXvfb/Openboxを起動し、共通AIFactory MCP経由で画面取得・クリック・日本語入力・キー・スクロール・返却を実行した。41行・1,464文字の入力結果はテスト用GUIアプリ内の全文と一致した。

一般ユーザーからワーカートークンとジャーナルを読めないこと、シェル変数の展開、終了コード7の保持、正常終了・timeout時の子孫プロセス停止、symlinkの先を削除しない返却、ワーカー強制終了時の操作停止と未確定状態の保持・復旧も確認した。既存のログイン画面、ネイティブWayland、個別の製品ビルドはこの試験の対象外。

Linux backendの実際の転送処理でも、computer MCPの設定配布、MCPによるPNG生成、日本語成果物の回収・ハッシュ検証・返却を確認した。この転送試験ではGitHubのcloneとLLM実行は置き換えており、製品チケットの完走を確認した試験とは区別する。


2026-09-07には、Proxmox上に専用Linux VM（Ubuntu 24.04 amd64、4 vCPU、4 GiB RAM、40 GiBディスク）を作成し、`linux-worker-01` として導入した。ワーカーとXvfb/Openboxはsystemdサービスとして動作する。Proxmoxはインスタンスの作成・ネットワーク設定に使用し、ワーカーの実行処理はProxmox APIを使わない。

このVMでも共通MCP経由の画面取得・クリック・日本語入力・キー・スクロール・返却を確認し、41行・1,464文字のGUI入力が全文一致した。管理用SSHはホストからの鍵認証に限定し、専用ネットワークからのプライベートネットワーク接続を制限している。


修正後の実機チケット では、GitHubからの取得、一般ユーザーでのClaude実行、computer MCPによる `Alt+F2 → gmrun → Mousepad` の起動、日本語3行の入力・画面確認まで成功した。GUI保存ファイルも管理側で読み取り、指定文字列との一致を確認した。初回の試験 で見つかった同一座標クリックの待機不具合とランチャー不足は修正し、連続クリックの回帰テストを追加した。VM再起動後の名前解決、サービス自動起動、制御系への再接続も確認済み。
