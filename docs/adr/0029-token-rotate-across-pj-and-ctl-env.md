# ADR 0029: 長期トークンの差し替えは `sandbox token rotate` 1 コマンドで、global・全 PJ・`ctl.env` を同時に更新する

- 状態: Accepted
- 日付: 2026-09-08

## 状況

`claude setup-token` の長期トークンには期限がある。切れたときの置き場は 3 種類ある。

- `~/.config/sandbox/env`（global 既定）
- `~/.config/sandbox/pj/<pj>.env`（PJ ごと。ADR-0006。運用中は全ファイルが同じ値）
- `~/.config/aifactory/ctl.env`（制御系のプロセス用。intake が `claude -p` を呼ぶのに使う。ADR-0017）

これまでは `sandbox token set <pj>` を PJ の数だけ繰り返し、`ctl.env` は手で編集して `sudo systemctl restart aifactory-console`、貸出中があれば `sandbox reinject --all` を手で打っていた。手数が多く、2026-09-07 には停止済みの Mac 側で `token set` してしまい制御系に反映されない事故が起きた。発行日をどこにも記録していないので、期限が近いことにも気づけなかった。

## 決定

1. `sandbox token rotate [claude|gh]` を足す。1 回の入力（tty ならエコー無しの `read`、それ以外は stdin）で次を順に行う。
   - **更新**: `~/.config/sandbox/env`（常に）、`~/.config/sandbox/pj/*.env` のうち **その鍵の行を持つファイルだけ**、`~/.config/aifactory/ctl.env`（存在し、その鍵の行があるとき）
   - **一覧**: `[updated] <path>` を 1 行ずつ出し、末尾に「N 箇所更新 / M 箇所 skip」。値はマスク（`mask`）でしか出さない
   - **restart**: `ctl.env` を更新したときだけ、systemd に `aifactory-console.service` があれば restart する。`sudo -n` が通らなければ実行せず「次のコマンドを実行してください: `sudo systemctl restart aifactory-console`」を表示して終了コードは 0 のまま
   - **reinject**: 台帳に貸出中の task があれば `cmd_reinject --all` を呼ぶ
   この順序は、後段（restart / reinject）の失敗でファイル更新の結果が隠れないため。
2. `ctl.env` への書き込みは **該当行の置換だけ**にする。`_write_key <file> <key> <val>` が `^KEY=` 行と `^# KEY issued: ` 行だけを落として末尾に書き直し、`CONSOLE_TOKEN` や説明コメントには触らない。`token set` / `token clear` も同じ関数を使う。
3. トークンを保存するとき、直前の行に `# <KEY> issued: YYYY-MM-DD` を書く。`token show` は出どころのファイルからこれを読み「発行から N 日（issued YYYY-MM-DD）」を出す。日付が無い既存ファイルは「発行日不明」と出す。
4. `token show` の 1 行目に実行ホストの種別を出す。判定は `~/.config/aifactory/ctl.env` の有無（制御系 LXC を作る `25-control-lxc.sh` だけがこのファイルを置くので、手元のマシンには存在しない）。
5. 制御系でないホストで `rotate` を実行しても **止めない**。警告（制御系 LXC で実行してほしい旨）を出して続行する。

`aifactory-gh-refresh.timer` は oneshot で毎回ファイルを読み直すので restart は要らない。

## 理由

置き場が 3 種類あること自体は ADR-0006（PJ ごとのトークン層）と ADR-0017（制御系 LXC）から来ていて、それぞれ理由がある。減らすのではなく、**同じ値を入れる操作を 1 つにする**のが手数と事故の両方に効く。

「鍵の行を持つファイルだけ」を対象にするのは、上の層（global）に任せている PJ に鍵の行を新しく生やさないため。生やすと以後 rotate 漏れの影響範囲が広がる。

止めない（5）のは、Mac 運用の停止は運用上の決定であってコードの制約ではないからで、CLI が一方的に使えなくなる方が害が大きい。案内だけ出して判断は人間に残す。

自動 restart を `sudo -n` が通るときに限るのは、制御系の運用ユーザーは NOPASSWD sudo を持つ（`25-control-lxc.sh`）が、それ以外の環境ではパスワード入力で止まってしまうため。`console/bin/install.sh` の `[[ $EUID -eq 0 ]] || sudo="sudo"` と同じ判定を使う。

## 結果（トレードオフ）

- `reinject --all` は台帳の全 task に走る。トークンを差し替えていない PJ の VM にも env の書き直しが起きるが、冪等なので実害は無い
- VM の中で動いている `claude` は `/run/sandbox/env` を起動時にしか読まない。従来どおり VM 内での再起動が要る（ADR-0006 の既知のトレードオフ）
- 発行日コメントの無い既存ファイルは「発行日不明」と出る。1 度 `set` / `rotate` すれば記録される
- PJ ごとに別のアカウントを使う運用を始めた場合、その PJ ファイルが鍵を持っていれば rotate に巻き込まれる。rotate の後に `sandbox token set <pj>` で戻す。区別する仕組み（`--only <pj,...>`）は必要になってから足す
- `ctl.env` はこれまで人間だけが編集していたファイルで、初めてプログラムが書き込む。行置換に限ることと、`CONSOLE_TOKEN` が残ることをテスト（`workflow/tests/test_sandbox_token.py`）で固定した
- 発行日は「保存した日」であって、トークン自体の失効日ではない。目安として使う
