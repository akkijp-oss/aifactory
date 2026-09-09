# 日々の運用

トークンの更新、VM プールの管理、テンプレートの更新など、継続して使うために必要な作業をまとめています。障害時の確認箇所と対処方法も紹介します。詳しい運用手順と構築状況は `sandbox/OPERATIONS.md` と `sandbox/STATUS.md` を参照してください。

## 毎日

```bash
kanban/bin/kb list                       # 今の todo / review / blocked
sandbox ls                               # 貸出中の VM が残っていないか
sandbox gh-app status                    # App と install が生きているか
```

貸出中の VM が run なしで残っていたら（前日の `--keep` や中断）、`sandbox release <id>` で返します。

## トークン

### Claude の鍵（鍵プール）

VM の中のエージェントが使う Claude の鍵は、制御系の**鍵プール**で持ちます（ADR-0044 / ADR-0045）。入口は console の「鍵」画面（`#/keys`）か `sandbox keys` で、どちらも同じ `~/.config/sandbox/keys.json` を読み書きします。鍵ごとに「Fable に使う」「Opus・Sonnet・Haiku に使う」を決め、チケットを実行するときにモデルごとに 1 本選ばれます（最後に使ってから一番時間が経った鍵）。鍵を何本か登録しておくと、利用枠の消費を分散できます。

```bash
claude setup-token                                   # 鍵の値（sk-ant-oat01-…）を作る
sandbox keys add max-akki --fable --other --note "akki の Max プラン"   # 値は対話入力（エコー無し）か標準入力
sandbox keys list                                    # 登録してある鍵と、最後に使った日時・使用回数
sandbox keys set max-akki --disable                  # 使わないようにする（使っている VM には別の鍵が入り直る）
sandbox keys token max-akki                          # 値だけ入れ替える（期限切れのとき）
sandbox reinject <id>                                # 実行中の VM に今の鍵を入れ直す
```

`claude setup-token` の長期トークンには有効期限があります。切れると工程が `failure: key` で止まり、チケットは `blocked` になります。`sandbox keys list` の登録日で期限が近いことに気づけます。

**`sandbox token set <pj>` は非推奨です。** PJ ごとの設定ファイルに鍵を置く古い方式で、互換のため動きますが、プールに合う鍵があるときは使われません（実行すると注意が出ます）。`sandbox token rotate` は、intake が使う `ctl.env` の鍵と、プールに合う鍵が無いときの保険（`~/.config/sandbox/env`）を差し替えるためのもので、プールは触りません。

### Claude の鍵プール（制御系にまとめて置く）

鍵が何本もあるときは、プロジェクトごとに配る代わりに、制御系の `~/.config/sandbox/keys.json` に名前を付けて並べておけます。VM を貸し出すとき（`take`）に、系統ごとに 1 本ずつ選んで渡します。

```bash
sandbox keys add fable-main --fable      # Fable 用の契約の鍵（値は対話入力）
sandbox keys add opus-a --other          # Opus / Sonnet / Haiku 用
sandbox keys list                        # 名前・フラグ・末尾 4 文字・最終利用・使っている貸出
sandbox keys set opus-a --disable        # しばらく使わない（使っている貸出には reinject で切り替える）
```

選ばれるのは、フラグの合う鍵のうち最後に使ってから最も時間が経ったものです。同じチケットの `reinject` では同じ鍵を使い続け、その鍵が使えなくなったときだけ選び直します。候補が 1 本も無い系統は、上のプロジェクトごとの鍵に落ちます（プールが空なら今までどおりです）。コンソールの「鍵」画面からも同じことができます。詳しくは [sandbox CLI の keys](../reference/cli-sandbox.md) と ADR-0044 を見てください。
### 利用枠切れ（トークン切れ）は自動で続きから再開する

鍵の**利用枠**（5 時間 / 7 日の窓）を使い切ると、エージェントの工程は `claude -p` が拒否されて止まります。runner はこれを普通の失敗と分けて扱い、途中までの変更を `wip: usage limit` としてコミットして退避ブランチに保全し、チケットを **未着手（todo）に戻します**（メモに「一時停止」と解除見込み時刻）。制御系の systemd timer `aifactory-resume.timer` が 5 分ごとに `dispatch --resume-paused` を呼び、解除時刻を過ぎたものから `kb run <id> --from` で**続き**（同じ工程を退避ブランチの上で）を回します。人が何かする必要はありません（ADR-0043）。

```bash
kb resumable                             # 一時停止中のチケットと、いつから続きを回せるか
dispatch --resume-paused                 # 今すぐ回す（timer と同じこと）
journalctl -u aifactory-resume           # timer のログ。回したものは workspace/logs/dispatch.log にも
kb run <id> --from                       # 手で続きを回す（メモに入っているコマンド）
```

- 解除時刻が記録に無い回（CLI の出力に `rate_limit_event` が無かった）は、止まってから `AIFACTORY_RESUME_BACKOFF_MIN` 分（既定 30）で試します
- 続けて `AIFACTORY_RESUME_MAX_HITS` 回（既定 6）止まったら自動再開をやめて `blocked` にします（鍵の枠が小さすぎる等）。どちらも `ctl.env` で変えられます
- 鍵そのものが無効・失効・残高不足（`failure: key`）のときは待っても戻らないので `blocked` です。上の手順で鍵を直してから `kb run <id> --from` で続きを回します

### GitHub のトークン（自動）

GitHub App の installation token は 1 時間で切れます。制御系の systemd timer `aifactory-gh-refresh.timer` が 45 分ごとに貸出中の VM へ払い出し直し、runner もスクリプトが担当する工程の前に払い出し直します。手動なら:

```bash
sandbox gh-app refresh                   # 貸出中の全 VM
sandbox gh-app token <pj>                # 払い出しの確認（トークンが表示される）
```

App の権限を足したとき（Actions: Read など）は、各 installation で承認が要ります。承認後は CLI が自動で要求します。

## プール

| やりたいこと | コマンド |
|---|---|
| 貸出状況 | `sandbox ls` |
| 定義台数と実体台数の食い違いを見る | `sandbox status [pj]`（`PJ DEFINED ACTUAL LENT FREE`）。コンソールの sandbox 画面と MCP の `sandbox_status` も同じ 4 つを出す |
| 空きなしで `take` が失敗 | エラー文の内訳（定義 / 実体 / 貸出 / 未構築 / clean 無し）を読む → 貸出中で不要なものを `release`、実体が足りなければ台数を増やす |
| 台数を増やす | `TPL_VMID=911x sandbox/proxmox/run.sh 40-pool.sh <pj> <台数>` → `50-firewall.sh`。`lvs pve/data` の `data%` を見る |
| 汚れた VM を作り直す | `qm destroy <vmid>` → `40-pool.sh`。`clean` がない VM は `reset` できない |
| 貸出中の VM を触らない | `~/.config/sandbox/state.json` を読んで飛ばす（`50-firewall.sh` は `LENT=` で飛ばせる） |

### 使われていない VM は自動で止まる

貸し出されていないプール VM は、最後に使われてから 24 時間（既定）で**停止候補**になり、候補が 10 台（既定。足切り）を超えたぶんだけ最終利用の古い順に止まります。候補が 10 台以下なら止まりません。制御系の systemd timer `aifactory-idle-stop.timer` が 15 分ごとに `sandbox idle-stop` を呼びます（ADR-0033 / ADR-0035）。`sandbox ls` の STATUS が `stopped` でも、たいていは故障ではなく節電です。表の後に `[idle-stop] N 台が節電で停止中` と出ます。

**手で起こす必要はありません**。次の `take` が自動で起動し、`[start] vm <vmid>: 停止中だったので起動した（N 秒）` を出します（そのぶん 30〜60 秒ほど余分にかかります）。

```bash
sandbox idle-stop --dry-run              # 何が止まる判定になるか、止めずに見る
journalctl -u aifactory-idle-stop        # timer のログ
```

止めたくないときは `~/.config/sandbox/env` に `SB_IDLE_STOP_HOURS=0`（全体）か、`~/.config/sandbox/pj/<pj>.env` に同じ行（その PJ だけ）を書きます。時間を変えるときは `24` の代わりに時間数を、足切りの台数を変えるときは `SB_IDLE_STOP_KEEP=<台数>`（全体のみ。`0` で候補を全部止める）を書きます。

## テンプレートの更新

| 層 | いつ | 手順 |
|---|---|---|
| プロジェクト層（依存の追加、seed の変更） | プロジェクトの Gemfile / package.json が変わったとき | `32-pj-template.sh` で再作成 → `40-pool.sh` でプール作り直し → `50-firewall.sh` |
| base 層（OS パッケージ、ツール、Claude Code の版） | 月 1 回 | `30-base-template.sh` → 全プロジェクトの 32 → 40 → 50。頻繁にやるとプロジェクト層の作り直しが負担なので月 1 |

作り直す前に `sandbox ls` で貸出中がないことを確かめます。

## develop から main への昇格（人間）

aifactory 自身の PR の宛先は `develop` です。ゲートが緑・レビューが PASS・CI がすべて pass の PR は runner が `develop` へ自動マージします（[`auto_merge`](../reference/project-yml.md)。ADR-0042）。**`main` への昇格は人間が行います。**

```bash
gh pr create --base main --head develop --title "develop → main（昇格）" --body "自動マージ済みの run: #.. #.."
gh pr checks <番号> --watch
gh pr merge <番号> --merge
```

- 昇格は「`develop` が緑で、実機で 1 周回せたら」。急ぐ理由が無ければ 1 日 1 回で足ります
- `develop` が赤いまま昇格しません。直す PR を先に `develop` へ入れます
- 制御系への配備（`bin/ctl-update`）は `origin/main` 追随のままです。`develop` の中身を試すときだけ `bin/ctl-update --ref origin/develop` を使い、確かめたら `bin/ctl-update` で戻します

## PJ 定義の変更手順

runner が読む PJ 定義（`examples/projects/<pj>/` の `project.yml` / `gates.sh` / `provision.sh`）は、制御系の checkout の作業ツリーから直接読まれます。制御系で `gates.sh` を直すと次の gates からその場で効きますが、制御系の remote は https なので `git push` はできません。直したまま放っておくと、制御系が origin と食い違ったまま本番が動きます。

基本は**手元（Mac）の checkout で直して push し、制御系では `bin/ctl-update` で配備するだけ**にします。急ぎで制御系の中で直したときは `git format-patch origin/main --stdout` で持ち出し、手元から push してから制御系を `git reset --hard origin/main` で揃え直します。

食い違い（push していないコミット・取り込んでいないコミット・未コミットの変更）は、コンソールのボードに警告として出ます。手順の全文と、制御系から直接 push できるようにする deploy key の付け替えは `sandbox/OPERATIONS.md` の「PJ 定義の変更手順」にあります。

## 通信制限（ファイアウォール）

VM はインターネットと sb-gw の DNS にだけ出られます（ADR-0010）。LAN・Proxmox ホスト・隣の VM・tailnet には届きません。テンプレートやプールを作り直したら `50-firewall.sh` を再実行して、`clean` スナップショットにファイアウォール設定が含まれるようにします。

確認（汎用 VM を take して中から）:

```bash
sandbox take generic 999
sandbox ssh 999 'curl -sI https://github.com | head -1; ping -c1 -W1 <LAN 内のホストの IP> || echo "LAN 不可 (期待どおり)"'
sandbox release 999
```

## 障害と対処

| 症状 | 見るところ | 対処 |
|---|---|---|
| `task-xxx.sb.internal` が解けない | `dig sb-gw.sb.internal`、Tailscale の split DNS | split DNS が消えていないか。`ssh root@10.77.0.2 systemctl status dnsmasq` |
| 10.77.0.2 に ping 不可 | Tailscale 管理コンソールの route 承認、`pct exec 9000 -- journalctl -u tailscaled -n 20` | ACL の grant がなければ足す。急ぎなら `SB_JUMP=<PVE_HOST と同じ値>` で Proxmox ホスト経由 |
| VM に ssh 不可 | `qm status 92NN`、`qm agent 92NN network-get-interfaces` | `qm start`。起動していれば `qm terminal` でシリアルから |
| `reset` が失敗 | `qm listsnapshot 92NN` に `clean` があるか | なければ破棄して `40-pool.sh` |
| VM から外に出られない | `iptables -t nat -S \| grep 10.77`、`pve-firewall status` | SDN 再適用 `pvesh set /cluster/sdn`。LAN・他 VM・tailnet 宛ては仕様で不可 |
| Mac から VM に届かない（ファイアウォール有効化後） | `/etc/pve/firewall/<vmid>.fw`、`qm config <vmid> \| grep firewall` | `50-firewall.sh` を再実行 |
| Claude Code が認証エラー（`failure: key`） | 実行記録の `key=… (pool: <名前>)` でどの鍵か分かる。`sandbox keys list` の登録日 | `claude setup-token` → `sandbox keys token <名前>`（値の入れ替え）か「鍵」画面。intake の鍵は `sandbox token rotate`。止まった run は `kb run <id> --from` で続きから |
| 工程が「利用枠の上限」で止まった（チケットが未着手に戻り、メモに一時停止） | `kb resumable`、`journalctl -u aifactory-resume` | 何もしない。解除時刻を過ぎると timer が続きを回す。急ぐなら「鍵」画面で別の鍵を足して `dispatch --resume-paused` |
| Proxmox ホストが落ちた | `ssh $PVE_HOST` 不可、`pvecm nodes`（クラスタなら別ノードから） | 電源を入れる（WoL / IPMI / 物理ボタン）。プールは onboot=0 なので手で `qm start` |

## 定期メンテナンス

- 月 1: base テンプレートの OS 更新
- 鍵の期限が近づいたら（`sandbox keys list` の登録日）`claude setup-token` → `sandbox keys token <名前>`（intake 用の `ctl.env` は `sandbox token rotate`）
- `workspace/runs/` が増えたら、古い run を消すか別置きにする（記録としては `state.json` と `work/` があれば十分）
- 完了したチケットが増えても、通常は削除する必要はありません。履歴は `kb list --all` で確認できます
