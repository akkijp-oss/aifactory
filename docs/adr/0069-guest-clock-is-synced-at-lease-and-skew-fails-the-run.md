# ADR 0069: ゲストの時計は貸出のたびに合わせ、それでもずれていたら run を止める（記録の日付は「合っている」を前提にしない）

- 状態: Accepted
- 日付: 2026-09-13

## 状況

2026-09-13 に develop へ着地したコミットのうち 2 本（`b325f15` / `549d839`）の author date が **2026-09-07** で、
同じ日の他のコミットと 6 日ずれていた。GitHub 側で作られるマージコミットはすべて正しい日付なので、
ずれているのは **ゲストの中の時計だけ**である。

原因は、プール VM の貸し方そのものにある。

- `sandbox/proxmox/40-pool.sh` はプール VM の snapshot `clean` を **RAM 込み**で取る。
  `take` / `reset` / `release` はそれを `qm rollback` で復元する（`sandbox/bin/sandbox`）。
  復元されたゲストは **snapshot を取った時刻から時計が再開する**。
- ゲストの systemd-timesyncd は動いているが、ポーリング間隔は成功後に伸びていく（既定の上限 34 分 8 秒）。
  実測した VM の journal では、復元直後の `01:25`〜`01:55` が 09-07 のまま進み、
  `2026-09-13T18:37:57 Contacted time server` で初めて 6 日 17 時間跳んだ。
- つまり **貸出から最大 34 分はずれたまま**。その窓の中でコミットや ADR 書きが済む run ほど嘘の日付になる
  （速い run ほど間違い、遅い run ほど正しくなる）。

波及はコミットの日付だけではない。

- `git log` の時系列が壊れ、`--since` / `--until` が取りこぼす。`git log --follow --diff-filter=A` で
  「いつ足されたか」を測ると嘘の答えが返る（本件の調査中に実際に誤った結論が出た）。
- ADR の日付欄は agent が `date` を読んで書くので、そのまま入る。`2026-09-07` を持つ ADR が 10 枚あり、
  番号順に並べると 6 箇所で日付が逆転している。
- ADR-0026（記録する時刻はオフセット付き ISO 8601）は **形式**の取り決めであり、値が正しいことは保証しない。

チケットの当初の見立て「NTP が有効になっていない」は実態と違った。テンプレートは Ubuntu 既定の
systemd-timesyncd が有効（`System clock synchronized: yes` / `NTP service: active`）で、
`timedatectl set-ntp true` を足すだけでは直らない。直すべきは **復元直後の窓**である。

## 決定

1. **貸出のたびに合わせる**。`sandbox take` / `sandbox reset` は、env を注入する前に `sync_clock <ip>` を通る
   （巻き戻しの有無に関わらず毎回）。順序は「時計 → env」。逆にすると `GH_TOKEN_EXPIRES_AT` が嘘の時刻で入る。
   `sync_clock` は (a) ゲストと制御系の epoch を比べ、`SB_CLOCK_TOLERANCE_S`（既定 120 秒）以内なら何もしない、
   (b) 超えていれば `systemctl try-restart systemd-timesyncd` で即座に 1 回ポーリングさせ、
   `SB_CLOCK_WAIT_S`（既定 20 秒）まで 1 秒ごとに測り直す、(c) 収束しなければ制御系の時刻を
   `sudo date -u -s @<epoch>` で入れて測り直す、(d) 前後のずれを `[clock] guest offset 578400s → 0s` の形で必ず出す。
   **合わせきれなくても take は失敗させない**（[warn] を出して続ける）。止めるかどうかは run の判断（決定 2）。

2. **合っていることを前提にせず、run の側で必ず測って止める**。`workflow/bin/run` は take の直後・checkout の前に
   `check_clock()` を通り、`AIFACTORY_CLOCK_TOLERANCE_S`（既定 120 秒）を超えていたら工程を 1 つも始めずに
   `failed` / `failure: "clock"` で終える。probe が数字を返さなければ同じく止める（測れなかったものを
   「ずれていない」と読み替えない）。ssh 自体が通らないときは従来どおりの失敗として上げ、「時計が測れない」と言い換えない。
   測れた値は成否に関わらず `state.json` の `clock_offset_s` に残す（どの run がどれだけずれていたかを後から数えられる）。
   `kb` はチケットを `blocked` にし、次の一手（`sandbox reset` してから回し直す）を note に書く。console は `clock_skew` として見せる。

3. **テンプレートのポーリング間隔を縮める**。`31-provision-base.sh` に `timedatectl set-ntp true`（既定に依存しない明示）と
   `/etc/systemd/timesyncd.conf.d/aifactory.conf`（`PollIntervalMaxSec=64`）を足し、復元後の自己回復を
   最大 34 分から約 1 分にする。**これはテンプレートを焼き直すまで効かない**（焼き直しはオーナー領分）。
   効いていなくても決定 1 と 2 で症状は消える。

4. **許容は 120 秒**。コミットの順序が逆転しないためには 1 分未満で足りるが、巻き戻し直後の
   ポーリング 1 回分の遅れを許すために 2 分を採る。env で変えられる。

5. **`git log` の日付で ADR や変更の前後関係を推定しない**。2026-09-13 以前の履歴には、この症状で付いた
   嘘の author date が混ざっている。順序が要るときはマージコミットか PR 番号を見る。
   既にずれている ADR 10 枚の日付は本 ADR では直さない（原因を塞いでからの別票）。

## 影響

- 時計がずれた VM を引いた run は、agent を 1 つも起こさずに `blocked` で戻る。VM 時間と鍵の枠は焼かない。
  代わりに、NTP が全く届かない網では決定 1 の (c) が効かない限り run が止まり続ける（そのときは警告が take のログに残る）。
- `sudo date -u -s` は「制御系の時計が正しい」ことを前提にする。制御系 LXC は Proxmox ホストの時計を共有するので受け入れる。
- 測るのは貸出直後の 1 回だけ。kvm-clock のゲストが走行中にずれ直すことは想定しない。
  途中で `sandbox reset` した場合は、その reset が `sync_clock` を通る。
- pull backend（macOS / Windows / Linux）も同じ `check_clock()` を通る。probe だけ OS ごとに差し替える
  （Windows は `[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()`）。Mac ゲスト（tart）は RAM snapshot を戻さないので
  同じ機構ではずれない見込みだが、**測らずに「ずれない」とは言わない**。

## 代替案

- **snapshot を RAM 無し（`--vmstate 0`）にする**。時計は起動のたびにホストから取り直されるので原因ごと消えるが、
  貸出ごとの起動待ちが数十秒伸びる設計変更で、プールの作り直しも要る。本 ADR では採らず、人の判断に回す。
- **Proxmox が rollback 後に qemu-guest-agent の `guest-set-time` を呼ぶ**。ホスト側（オーナー領分）の変更になり、
  制御系からは強制できない。sandbox CLI からゲストに入って合わせる方（決定 1）で同じ結果が得られる。
- **run 側の検知だけ入れて、合わせるのはやめる**。NTP が届かない網では毎回止まるだけになるので採らない。
- **ADR の日付を agent に書かせず、制御系が後から埋める**。ADR は agent がゲストの中で書くものなので、
  書く場所を変えるより時計を正す方が影響が小さい（コミットの author date も同時に直る）。
