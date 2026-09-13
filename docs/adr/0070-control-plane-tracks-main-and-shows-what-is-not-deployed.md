# ADR 0070: 制御系は `main` 追従のままにし、「develop に着地したが未配備」と「配った PJ 定義と正本のズレ」を機械で見せる

- 状態: Accepted
- 日付: 2026-09-13

## 状況

制御系（ctl）の checkout は `main` 追従で、runner（`workflow/bin/run`）・`sandbox` CLI・console・kanban は
すべてそこから動く。PJ 定義（`examples/projects/<pj>/` の `project.yml` / `gates.sh` / `provision.sh`）も
そこから VM へ配られる（`workflow/kit/steps/gates.sh` の `scp "$PROJECT_DIR/$GATES"`）。
一方 aifactory 自身の PR は `develop` に着地する（`base_branch: develop`）。

この 2 つの間に、**着地から `main` 昇格 + `bin/ctl-update` までのあいだ「着地したのに 1 行も効いていない」窓**がある。
2026-09-13 に 2 つ実測した。

- `unittest-pull`（`workers/tests` を守るゲート）は #446 で `develop` に足されたが、
  #477 / #478 の run のゲートは **PASS 8 行**で、`unittest-pull` は FAIL でも INFO でもなく **行そのものが無かった**。
  `origin/main:examples/projects/aifactory/gates.sh` は 8 ゲート、`origin/develop` は 9 ゲートで、差はちょうど 1 行。
- #491（ゲストの時計ずれ）は `11:36:01Z` に `develop` へ着地し、その **108 秒後**に始まった #492 の run のコミットは
  なお 6 日ずれた author date を持っていた。ctl の checkout に `sync_clock` / `check_clock` が無かったため。

害は「守りが減ること」そのものより、**減ったことが誰にも見えないこと**にある。ゲートは赤くならず
「全部緑」は嘘にならない。`known_red_gates` の格下げ（FAIL → INFO）とは別経路で、行が黙って消える。
実際 PM は #491 を「着地＝有効」と誤記し、後から実測して訂正した。

既存の防御は届かない。

- チケット 337 の警告（`repo_status()`）は `HEAD...@{upstream}`（= `origin/main`）との比較なので、
  「`main` が `develop` より 1 リリース古い」状態を **clean と表示する**。これが本件の穴である。
- チケット 247 の `bin/ctl-update` は配備手段であって、**追従先の選択**は扱っていない。

## 決定

1. **ctl の追従先は `main` のまま変えない。** `main` は `develop` から人間が昇格させる安定版という設計（ADR-0042）を
   崩さない。「`bin/ctl-update` を `develop` にすれば済む」には流れない。窓を無くすのではなく、**窓を見せる**。
2. **「PJ の base に着地済みで、この制御系に未配備」をボードと `overview` に出す。**
   `console/lib/core.py` の `repo_status()` に `bases` / `undeployed` / `fetched` / `fetch_error` を足す。
   判定は `origin/main` ではなく **`origin/<base_branch>`** との比較で、`HEAD..origin/<base>` の件数と直近 5 件の
   件名を返す。`diverged`（337 の意味）は変えず、未配備は別のキー・別の警告行にする。
3. **比べるのは「この checkout と同じリポジトリを見ている PJ」だけ。** `project.yml` の `repo` と
   `git remote get-url origin` を `owner/name` に揃えて突き合わせ、一致する PJ の `base_branch` だけを見る。
   他 PJ の `base_branch` は別リポジトリのブランチ名なので、kumitate の `develop` を aifactory の
   `origin/develop` と比べると嘘の警告になる。上流と同じブランチ（`origin/main`）は既存の ahead / behind が
   担うので base の一覧からは外す（同じ比較を 2 回出さない）。
4. **console は読むためだけに `git fetch` する。** 着地の直後は ctl の `origin/develop` が古いままなので、
   fetch しないと「着地した瞬間」を検知できない。上限は `REPO_FETCH_TTL`（300 秒）に 1 回・`timeout` 15 秒・
   対象ブランチだけ・**HEAD も作業ツリーも動かさない**・失敗は例外にせず `fetch_error` に落とす。
   網や認証を嫌う運用は `CONSOLE_REPO_FETCH=0` で fetch だけを止められる（件数は最後に取り込めた時点のものになる）。
5. **配った PJ 定義と正本のズレは `gates.txt` の先頭に 1 行出す。** `gates.sh` は VM へ配る直前に
   `git diff --name-only origin/$BASE -- .` で PJ 定義ディレクトリを正本と比べ、差があれば
   `INFO pj-drift <rel> differs from origin/<base>: gates.sh (missing: unittest-pull) provision.sh` を足す。
   ゲートは名前まで出す（本題は「どのゲートが走っていないか」）。
6. **`pj-drift` は INFO であって FAIL にしない。** ズレは配布経路の問題で実装役の変更ではない。FAIL にすると
   automerge も implement への差し戻しも FAIL だけを見る（ADR-0042 §4）ので、全 run が実装役へ戻ってしまう。

## 理由

- **4 と 5 の両方を入れる。** 4（可視化）だけだと、次に同じ型が `provision.sh` や `project.yml` で起きたときに
  「何が配られていないか」がファイル名まで出ない。5（検知）だけだと、ゲート以外の制御系コード
  （runner・`sandbox` CLI・console）の未配備が見えない。害の本体は「黙って通ること」なので、
  配布経路（5）と制御系そのもの（4）の両方に目を付ける。
- **`repo_status()` に足す。** 「ctl の checkout が正本とどうずれているか」の判定は 1 か所という
  ADR-0015 の約束をそのまま延長する。画面と MCP は組み立てるだけにする。
- **fetch の副作用を最小にする。** `git fetch` は remote-tracking ref しか動かさないので、作業ツリーにも
  HEAD にも影響しない。それでも console が 5 秒ごとに網を叩くのは避けたいので TTL と timeout で囲い、
  失敗したときは「取り込みができなかった」と言うだけにして他の機能を止めない。
- **ズレの検出を `gates.sh` に置く。** 配るのはこの script なので、「配った版」と「正本」の突き合わせは
  配る場所でやるのが一番ずれにくい。`git` が無い・PJ 定義が追跡外（`workspace/projects/`）・`repo` が違う
  ときは黙る（比べる正本が無い）。

## 結果

- ボードに「`origin/develop` に着地済みで、この制御系にまだ配備されていないコミットが N 件あります（PJ: aifactory）」と
  直近のコミット件名が出る。MCP の `overview` にも `repo.undeployed` / `repo.bases` として載る。
  昇格して `bin/ctl-update` を通すと消える。
- `gates.txt` の 1 行目に `INFO pj-drift …` が出る回がある。run は止まらない（FAIL ではない）。
  console のゲート一覧には `pj-drift` が INFO として並ぶ。
- 窓そのものは残る。運用としては **昇格を溜めない**のが本筋で、この ADR が足すのは「溜まっていることが
  見えている」状態だけである。
- 本件の変更自身も `develop` に着地するので、**昇格までは効かない**（同じ窓の中にある）。
- `CONSOLE_REPO_FETCH=0` にすると未配備の検知は「最後に取り込めた時点」に退化する。これは意図した逃げ道である。

## 関連

- ADR-0042: `main` は `develop` から人間が昇格させる安定版（追従先を変えない根拠）
- チケット 337（PR #41）: ctl の checkout が origin と食い違っていたら overview に出す（`origin/main` との比較）
- ADR-0015: 読み書きの判定は `console/lib/core.py` に 1 つ
- ADR-0038: base でも赤いゲートの INFO 格下げ（`pj-drift` は別経路で、行が消えるほうの問題）
