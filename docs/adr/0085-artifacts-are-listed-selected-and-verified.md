# ADR 0085: VM の成果物は「列挙 → 上限で選別 → 1 接続で取得 → 届いたか検証 → 記録」で回収する。落としたものは必ず理由が残る

- 状態: Accepted
- 日付: 2026-09-16

## 状況

Proxmox backend の回収は run の終わりに 1 回だけ走る `Run.release()` の 1 行だった。

```
sh(["scp", "-r", *self.scp_opts(), f"dev@{ip}:{self.work}", str(self.run_dir / "work")], check=False)
```

`scp -r` はファイルの種別を選ばないので、`report.md` / `plan.md` / `research.md` / 画像は **元から対象**だった。
それでも #520 / #521 / #524 / #536 では、管理役が `read_file` で `runs/<run>/work/report.md` を開くと「ファイルが見つかりません」
になり、スクリーンショットは人が手で run の `work/` へ運んでいた。読めなかった理由は「対象から漏れていた」ではなく、
**回収が届いたかどうかを誰も見ていなかった**ことにある。

- `check=False` で終了コードを見ていない。接続が切れてもコピーが 0 件で終わっても、ログにも `state.json` にも何も残らない。
  後から「VM に無かった（自死して report.md を書けなかった）」のか「在るのに運べなかった」のかを切り分ける材料が無い。
- 上限が無い。`agent-*.jsonl` 相当の大きなファイルや PJ が作った成果物を無制限に運ぶ。落としたときの記録も無い。
- `scp -r` は宛先ディレクトリが既にあると `work/<task-id>/report.md` に 1 段ネストする。`read_file` は `work/report.md` を探す。
- 同じ役目を持つ pull backend（macOS / Windows / Linux）には、件数・合計の上限、検証、`state.json["artifacts_skipped"]`
  への記録が既にある（`workflow/lib/macos.py`）。Proxmox 側にだけ無い。

## 決定

1. **回収は 4 段にする**（`workflow/bin/run` の `Run.collect_artifacts()`）。
   1. **列挙**: `ssh` 1 回で `find` を打ち、`種別 \t サイズ \t 相対パス` を NUL 区切りで受ける。`work/` が無ければ rc 3。
   2. **選別**: 純関数 `select_artifacts()` が上限と優先順で accepted / skipped を決める。
   3. **取得**: accepted の名前を NUL 区切りで `tar -cf - --null -T -` の標準入力に渡し、tar を 1 接続で受ける。
   4. **展開と検証**: `extract_artifacts()` が accepted に挙げた通常ファイルだけを `runs/<run>/work/<相対パス>` に置き、
      届かなかったものを `missing` として記録する。
2. **回収するのは `work/` 直下の通常ファイルと、`gates/` `attachments/` の中身だけ**（`ARTIFACT_SUBDIRS`）。
   それ以外のディレクトリ・symlink・非通常ファイル・危険な名前は運ばない。
3. **上限は定数で固定する**: 1 ファイル 4 MiB（`ARTIFACT_FILE_MAX`。console の `read_file` が画像を返せる上限と同じ）、
   合計 32 MiB（`ARTIFACT_TOTAL_MAX`）、256 件（`ARTIFACT_COUNT_MAX`）。`project.yml` や env の項目は増やさない。
4. **上限に当たったときに何を残すかは優先順で決める**: 直下の `*.md` / `*.txt` → `gates/` → `attachments/` → 画像 → その他。
   同順位は名前順。報告書とゲートログが真っ先に残る。
5. **落としたものは黙って消さない**。`state.json["artifacts_skipped"]` に `{name, reason, size}` を残し、ログにも 1 件ずつ出す。
   理由は `directory` / `symlink` / `non-regular` / `size` / `total` / `count` / `name` / `missing` / `unsafe`。
6. **結果を `state.json` に残す**: `artifacts_received`（accepted が全部届いたか）/ `artifacts_count` / `artifacts_bytes` /
   `artifacts_skipped` / `artifacts_error`。**「work/ が元から無い」は `artifacts_error: "no work dir"`** として、
   接続や取得の失敗と別の記録にする。
7. **回収がどこで失敗しても例外を外に出さず `sandbox release` まで進む**（今までと同じ）。pull backend は失敗すると lease を
   保持するが、Proxmox は同じにしない。
8. **console と MCP は変えない**（ADR-0040 の決定 7 のまま）。`runs/` に入りさえすれば `run_show` の一覧に載り
   `read_file` で読める（画像は image で返る）。

ADR-0040 の決定 1 のうち「**runner の回収処理は変えない**」だけをこの ADR が置き換える。gates.sh が
`$WORK/gates/<名前>.log` に書く形（決定 1 の前半と決定 2〜6）はそのままで、`gates/` は回収する場所として固定した。

ADR-0041 の決定 5（チケットの添付の控えは既存の回収経路に乗せる）は結果として保たれる。`attachments/` を
`ARTIFACT_SUBDIRS` に固定したので `runs/<run>/work/attachments/` に戻る。ただし 4 MiB を超える添付（attach の上限は
1 ファイル 20 MiB）は控えが戻らず `artifacts_skipped` に `size` として残る。原本は `workspace/attachments/<id>/` に
あるので失われない。

## 理由

- **なぜ `scp -r` をやめたのか。** `scp -r` には「何を運んだか」を返す口が無い。終了コードを見るだけでは
  「0 件だが成功」と「全部運んだ」を区別できず、上限もかけられない。列挙を先に取れば、落としたものに名前と理由が付く。
- **なぜ tar で 1 接続なのか。** 名前を `scp` の引数に並べると、空白や日本語の名前を安全に渡せない
  （OpenSSH 9 以降の既定は宛先をリモートシェルに通さず、レガシーは通す。`scp_files_to()` の説明と同じ問題）。
  名前を標準入力で渡す `tar --null -T -` なら quote の問題が起きず、接続も 1 回で済む。
- **なぜファイル単位で置くのか。** ディレクトリごと運ぶ規約（宛先の有無で深さが変わる）を踏まないため。
  `runs/<run>/work/` が既にあっても `work/report.md` の深さで置かれる。
- **なぜ tar の中身を信用しないのか。** VM の中で動くのは agent で、名前は agent が作れる。`..`・絶対パス・symlink の
  メンバーは展開せず `unsafe` として記録する。
- **なぜ失敗しても VM を返すのか。** Proxmox の VM は snapshot でテンプレートに戻せ、途中の作業はブランチに push 済み
  （`wip:`）で残る。回収の失敗のために VM を貸したまま止めると、プールの 1 台が塞がり後続の run が待つ。
  この判断を変えるならプール資源のトレードオフなので、別の ADR で決める。
- **なぜ上限を定数にするのか。** PJ ごとに変える理由が今は無く、`project.yml` の項目を増やすと PJ 定義の検証と文書が増える。
  値を変える議論が出たら、この ADR を置き換える形で別票にする。

## 結果（トレードオフ）

- ssh が 1 往復増える（列挙 1 回 + 取得 1 回。今までは scp 1 回）。
- `work/` の下の `gates/` `attachments/` 以外のディレクトリは回収しなくなる。共通の約束（各役割の依頼文）が
  「`work/` 直下の通常ファイルだけが回収される」と言っているので合わせた。runner / kit が別のディレクトリを作るように
  なったら `ARTIFACT_SUBDIRS` に足す。
- 上限を超えた成果物は run の記録に残らない（名前と理由だけが残る）。大きな成果物を残したい PJ は、
  中身を切り詰めるのは PJ 側の仕事になる（ADR-0040 の決定 3 と同じ扱い）。
- VM 側の `find -printf` と `tar --null -T -` は GNU 前提。Proxmox のテンプレート（Debian / Ubuntu）はこれを満たす。
  満たさない環境では列挙が失敗し、`artifacts_error` として記録に残って回収 0 件になる（黙って消えない）。
