# ADR 0058: pull backend の code step は kit の script を guest の中で走らせる（`SB_LOCAL`）。対応表はテストで固定する

- 状態: Accepted
- 日付: 2026-09-11
- チケット: 386

## 状況

ADR-0042 で `pr-automerge.sh` を全 workflow の最後の工程として足した。`kit/steps/*.sh` は Proxmox backend の
前提で書かれており、guest への 1 手は `sb() { sandbox ssh "$TASK" "$@"; }` である。pull worker（macOS / Windows /
Linux）には `sandbox ssh` が無い。ゲストへの命令は制御系の queue を通した guest-exec（lease 付き）だけで届く。

pull backend は起動時に「workflow の code step が自分の対応集合に入っているか」を検証し、外れていれば
`ValueError: unsupported pull-worker code steps: …` で run ごと止める。ADR-0042 でこの対応表を更新しなかったので、
**macos-pull の PJ は run を 1 つも始められなくなった**。実測は 2026-09-09 16:45 UTC の asura #381：guest を取る
前に rc=1 で終わり、runner の記録が無いままチケットが blocked になった。PR #66 で「auto_merge を書いていない PJ
では runner が automerge 工程を飛ばすので検証からも外す」を入れて起動は戻したが、**pull worker で auto_merge を
使う道は無いまま**である。asura は 2026-09-09 に 21 本の PR を人が手でマージしており、ADR-0042 の恩恵を最も受ける
PJ の 1 つでもある。

同じ形の落とし穴は今後も起きる。code step を足す人が 3 つの backend（`workflow/lib/macos.py` / `windows.py` /
`linux.py`）の対応表を更新する導線が無く、更新を忘れても気づくのは「その backend の PJ が起動できなくなったとき」
だからである。

## 決定

1. **pull backend は kit の code step を「guest の中で走らせる」形で通す。** backend は `kit/steps/<名前>.sh` を
   guest の `$WORK` に置き（`scp_to`）、guest-exec 1 本で `bash` に渡す。判定に使う `pr_url` / `gates.txt` /
   `review.md` は回収前の guest の `$WORK` にあるのでそのまま読め、`gh` は `runtime.env` の `GH_TOKEN`
   （PJ 限定の App token）で動く。制御系から guest のファイルを引き寄せてから判定し直す経路は作らない。
2. **script 側の切り替えは `SB_LOCAL` の 1 つだけにする。** `SB_LOCAL=1` は「この script 自体が guest の中で
   走っている」の意味で、`sb()` を `bash -c` に差し替える。マージするかどうかの判定ロジックは backend ごとに
   分岐させない（Proxmox と pull で「同じ条件で同じ結論」になることを 1 つの script で担保する）。
3. **各 backend は code step の対応表（`CODE_STEPS`）を持ち、分類は `run` / `noop` / `unsupported` の 3 つにする。**
   表に無い名前は `unsupported` と同じに扱う（足した人が表を更新していない、を「起動前に拒否」へ倒す）。
   Linux は macOS の表と実装をそのまま継承する（POSIX の guest なので分ける理由が無い）。
4. **runner が飛ばす step 名は `Run.SKIPPABLE_CODE_STEPS` の 1 か所に置く。** 「条件を満たさない PJ では工程ごと
   飛ばす」（今は `auto_merge` を書いていない PJ の automerge）という判断と、pull backend の起動時検証が
   同じ定数を読む。
5. **`workflow/tests/test_code_steps.py` で「`kit/workflows/*.yml` の code step が 3 つの backend すべてで
   分類済み」を固定する。** 実装の有無ではなく分類の有無を見る。`unsupported` のままにする判断（Windows の
   `pr-automerge.sh`、全 backend の `pr-merge.sh`）は残せるが、黙って忘れることはできなくなる。
6. **手順書に対応表と追加手順を書く**（[Macワーカーの導入と運用](../macos-worker.md#workflowのcode-step対応)）。

## 検討して採らなかった案

- **kit の step を backend ごとに書き直す**（`pr-automerge.macos.sh` など）。マージ条件の判定が 2 つ以上に増え、
  「Proxmox では止まるが Mac では通る」が起こりうる。取り消しにくい外向きの操作（`gh pr merge`）で条件が
  分岐するのは割に合わない。
- **判定を Python（backend）側に移し、script は Proxmox 専用のまま残す**。同じ理由で判定が二重になる。
  加えて ADR-0042 が「判定は 1 つの script」と決めた設計をこの票の都合で崩すことになる。
- **起動時検証をやめ、未対応の step は実行時に失敗させる**。`ValueError` は消えるが、agent を全部走らせて
  PR まで作った後の最後の工程で落ちる。VM 時間と鍵の利用枠を使い切ってから気づくので、今より悪い。
- **Windows も同時に実装する**（Git Bash 経由なら理屈の上では動く）。実機で確かめられないまま `gh pr merge` の
  経路を増やさない。表で `unsupported` と宣言し、`auto_merge` を書いた Windows の PJ は起動前に拒否する。
  必要になったら別票で、偽 bash で呼び出しの形を固定するテスト付きで足す。

## 結果

- macos-pull / linux-pull の PJ で `auto_merge` を設定した run が起動でき、gates 緑・review PASS・CI 緑のとき
  guest の中の `gh` が PR を base へマージする。マージした事実は backend が `note_merged()` で `state.json` の
  `merged` に残す（Proxmox は `bin/run` の `run_code` が呼ぶが、pull backend はそこを通らない）。
- automerge は CI 待ちのポーリングが guest の中で回るので、guest-exec を長く 1 本張る（`AUTO_MERGE_WAIT_MIN` 分
  + 15 分。ただし `run_remote` の上限 3600 秒）。`wait_min` が 45 分を超える PJ 設定では上限で切られ、
  そのときはマージせず PR を開いたまま人へ渡る（`NOMERGE` と同じ安全側の終わり方）。
- Windows の PJ で `auto_merge` を書くと起動前に拒否される。今までも automerge は動かなかったが、
  「途中まで進んでから黙って終わる」ではなく「最初に断る」になる。
- `merge-pr` workflow の `pr-merge.sh` は 3 つの backend すべてで `unsupported` のまま（この票の範囲外）。
  対応表に明示されたので、次に触る人はそれが「未実装」であって「見落とし」ではないと読める。
- 実機（Mac / Windows / Linux worker）での 1 run 実証はこの ADR の時点では出していない。偽 guest
  （`workflow/tests/test_macos.py` の `PullBackendAutomergeTest`）で経路の形まで固定してある。
