# ADR 0033: 使われていないプール VM は止める。起こすのは `take` の役目

- 状態: Accepted（決定 1 の「N 時間経ったら止める」は ADR-0035 で「N 時間で候補、候補が K 台を超えた分だけ止める」に改めた）
- 日付: 2026-09-08

## 状況

プール VM（`sb-<t>-<pj>-NN`）は、貸し出されていなくても常時 `running` のままだった（`sandbox ls` の STATUS が全部 running）。返却は `rollback`（snapshot `clean` へ巻き戻す）で終わり、電源は落とさない。VM 1 台あたりのメモリと CPU は借り手がいなくても Proxmox に予約されたままで、プールを増やすほど無駄が増える。

一方、**起こす側はすでにあった**。`rollback()`（`sandbox/bin/sandbox`）は巻き戻しのあとに「`pve_status` が running でなければ `pve_start` して ssh（22）が上がるまで待つ」を持っており、`take` / `reset` / `release` は必ずここを通る。つまり停止中の VM を `take` しても動く。足りなかったのは「使われていない VM を止める側」だけだった。

テナントの API トークン（ADR-0017 の `AifactorySandbox` ロール）には `VM.PowerMgmt` が入っているので、制御系 LXC は API モードのまま止められる。常駐の型も `aifactory-gh-refresh.{service,timer}`（45 分ごと）という先例がある。

## 決定

1. **止める判断は `sandbox idle-stop` の 1 コマンドに置く。** 貸出中でなく（`state.json` に vmid が無い）、最終利用から `N` 時間（既定 3。`SB_IDLE_STOP_HOURS`、`0` で無効。`pj/<pj>.env` で PJ 別に上書き可）経った **qemu のプール VM だけ**を止める。制御系の `ctl` / `gw` は LXC なので、API モードの `pve_vms`（qemu 限定）にも名前の除外（`-base` / `-gw` / `-ctl` / `-tpl-`）にも掛からず対象に入らない。
2. **起こすコマンドは作らない。** `sandbox start <vmid>` は増やさず、起動は `rollback()`（= 次の `take`）に任せる。ADR-0031 と同じく `take` の失敗モデルを増やさないため。
3. **最終利用は `last-used.json`（vmid → オフセット付き ISO 8601。ADR-0026）に別ファイルで持つ。** `take` / `reset` / `release` / `reinject` のたびに書く。貸出台帳（`state.json`）の書式には混ぜない。記録が無い VM は Proxmox の `status/current` の `uptime` で代用し、それも取れなければ**止めない**（安全側）。
4. **判定から shutdown 完了までを、1 台ずつ `state.json.lock` の中で行う。** 234 のロックを共有する。
5. **止め方は `status/shutdown`（timeout 120 秒）→ 止まらなければ `status/stop` の 2 段。** `forceStop=1` は使わない。
6. Proxmox の抽象（ADR-0017 決定 4 の 6 関数）に `pve_uptime` / `pve_shutdown` / `pve_stop` を足して **9 関数**にする。呼ぶ側はこの 9 つだけを使う規律は変えない。
7. 実行は制御系 LXC の systemd timer `aifactory-idle-stop.timer`（15 分ごと）。`install.sh --systemd` で gh-refresh と一緒に登録し、`--remove` で外す。macOS の launchd plist は作らない（Mac 側の運用は停止済み）。
8. 結果は `idle-stop.json`（`{hours, last_run, stopped: [{vmid, name, at, last_used}]}`）に書き、`sandbox ls` / `status` の脚注、コンソールの sandbox 画面、MCP `sandbox_status` が「節電で停止中」と説明できるようにする。

## 理由

- **なぜロックを shutdown 完了まで持つのか。** 判定だけをロックで囲んで先に手放すと、その直後に `take` が同じ VM を予約しうる。そのとき VM はまだ `running` で ssh も生きているので、`rollback()` は起動待ちを飛ばす。その後 shutdown が効いて VM が落ち、走り出した run が死ぬ。「止め終わるまで貸さない」が最小の安全策で、増える待ちは通常の shutdown（10〜20 秒）ぶんだけ。あわせてロックの待ち時間を `SB_LOCK_WAIT`（既定 150 秒）にした。
- **なぜ「不明」なら止めないのか。** 誤って止めても次の `take` が起こすので実害は小さいが、`uptime` が取れない状況は Proxmox 側の異常も含む。異常時に電源を触る側へ倒さない。
- **なぜ PJ 別の時間をサブシェルで読むのか。** `load_pj` は env を累積して `source` するので、PJ A の `SB_IDLE_STOP_HOURS=0` がそのまま PJ B の判定に漏れる。1 台ぶんの読み取りはサブシェルに閉じる。
- **なぜ別ファイル（`last-used.json` / `idle-stop.json`）なのか。** `state.json` は「いま誰に貸しているか」の正本で、返却時に項目が消える。最終利用は返却後こそ必要なので、寿命が違う。読み手（console / MCP）も貸出台帳とは別に読める方が壊しにくい。

## 結果（トレードオフ）

- 3 時間以上あいだが空いた最初の `take` は、起動と ssh 待ちで 30〜60 秒ほど遅くなる。待っていることが分かるよう、`rollback()` は起動したときだけ `[start] vm <id>: 停止中だったので起動した（N 秒）` を出す（runner のログにそのまま乗る）。
- `take` の待ちは idle-stop がロックを持つあいだ最大 `SB_LOCK_WAIT` 秒まで伸びうる。
- 常時起動にしたい PJ は `pj/<pj>.env` に `SB_IDLE_STOP_HOURS=0` を書く。設定が 2 段（全体 / PJ）になる。
- `qm status --verbose` の `uptime:` 行（ssh モード）は実機で未確認。取れなければ「不明 = 止めない」に倒れるので、最悪でも今までどおり動き続ける。
