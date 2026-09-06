# チケットを作る

依頼をチケットとして登録する方法を説明します。自由な文章から `intake` で作成する方法と、所定の形式で書いた本文を `kb new` で登録する方法があります。チケットの書き方と、登録後の修正方法も紹介します。

## チケットの形

チケットは Markdown ファイル 1 枚です。runner がそのまま読むので、形は固定です。

```markdown
# fix: seeds と dev-entrypoint を現行モデルに追随させ db:seed を通す
pr: 298                                  ← merge-pr のときだけ。対象 PR 番号

## 背景
sandbox のテンプレートを焼く際に db/seeds.rb が失敗する。…

## やること
- db/seeds.rb で Company.create! する箇所に担当チームを紐付ける
- …

## 完了条件
- クリーンな DB に対して bin/rails db:seed がエラーなく完走する
- bundle exec rspec が全件緑
- PR が作成されている
```

| 要素 | 決まり |
|---|---|
| 1 行目 | `# ` + 題名。runner がブランチ名（`sandbox/<id>-<wf>-<slug>`）と PR 題名に使う。接頭辞（`fix:` / `docs:` / `feat:` / `test:`）を付けると分かりやすい |
| 2 行目 `pr: N` | merge-pr のときだけ。対象 PR の番号 |
| 本文 | 意図と背景。推測で補った箇所は「（推測）」と印を付ける |
| `## 完了条件` | 機械や人がチェックできる形で。「テストが成功」「PR がある」「summary.md に X が書いてある」 |

プロジェクトと種別（ワークフロー）は本文には書きません。kanban が `pj` / `kind` として持ちます。

## 自由文からチケットを作成する（intake）

入力には、音声の文字起こし、Slack のメッセージ、箇条書きのメモなどを使えます。

```bash
glue/bin/intake memo.txt            # ファイルから
cat memo.txt | glue/bin/intake -    # 標準入力から
```

intake は次を 1 回の LLM 呼び出し（judgment クラス = Fable）で行います。

1. どのプロジェクトか、どの種別かの判定
2. 題名（50 字以内、接頭辞つき）の作成
3. 本文の整形と「完了条件」の追加

結果は `kb new` に渡され、id が返ります。本文の末尾に `（intake <日時> / model <モデル> / confidence 0.9 / <理由>）` が残るので、後から「LLM がどう判定したか」を読めます。

### 判定を先に見たいとき

```bash
glue/bin/intake memo.txt --dry-run
```

チケットを作成せず、判定結果の JSON（pj / kind / title / body / confidence / reason）だけを出します。

### プロジェクトや種別が分かっているとき

プロジェクトや種別が決まっている場合は、コマンドのオプションか本文の先頭行で指定できます。指定が重なった場合は、`--pj` / `--kind`、本文の先頭行、LLM の判定の順に優先されます。

```bash
glue/bin/intake memo.txt --pj kumitate --kind bug           # オプションで
printf 'pj: kumitate\nkind: chore\n依頼文…' | glue/bin/intake -   # 先頭 5 行以内の pj: / kind: 行で
```

指定した項目は LLM の判定より優先されます。指定していない項目の判定と、題名・完了条件の整形は LLM が行います。

### 判定が違っていたら

チケットの作成後に `kb set` で直します。runner は本文の `pr:` を読むので、`--pr` を変えると本文の行も書き換わります。

```bash
kanban/bin/kb set 204 --kind feature
kanban/bin/kb set 204 --pr 300
```

いつも同じ間違い方をするなら、`glue/bin/intake` 内の判定の目安（プロンプト）を直します。

## 整った本文からチケットを作成する（kb new）

チケットの形で本文を書けるなら、intake を通さず直接チケットを作成します。LLM は呼びません。

```bash
kanban/bin/kb new <pj> <kind> "<題名>" --body ticket.md
kanban/bin/kb new <pj> <kind> "<題名>" --body - <<'EOF'
本文…
## 完了条件
- …
EOF
kanban/bin/kb new kumitate merge-pr "PR #298 を develop へマージ" --pr 298 --body ticket.md
```

- `pj` はプロジェクト定義（`$AIFACTORY_WORKSPACE/projects/<pj>/`、なければ `examples/projects/<pj>/`）があるプロジェクト、`kind` は `workflow/kit/workflows/<kind>.yml` がある種別。どちらも存在を確認します
- id は連番（`MAX(id)+1`、最小 100）。DNS 名 `task-<id>.sb.internal` に使えるよう 3 桁以上です
- `--id N` で番号を指定できます（過去の記録を取り込むとき用）
- `--note` でメモ、`--status` で初期状態（既定 todo）を付けられます

## 良いチケットの書き方

| 良い | 悪い |
|---|---|
| 「calendar の表題テスト 2 件が 8 月固定のフィクスチャで時間依存。固定日時で解消して」 | 「テストが落ちるので直して」 |
| 完了条件に「rspec すべて成功」「既存テストを削除・skip しない」 | 完了条件なし |
| 範囲を書く。「seeds と entrypoint だけ。モデルは変えない」 | 範囲を書かない（エージェントが範囲外まで直す） |
| 本番影響の有無を書く | 書かない（planner が STOP を出して止まる） |

エージェントは「範囲外の問題を見つけたら直さずに報告書に書く」約束で動きます（`workflow/kit/roles/_common.md`）。変更してよい範囲を具体的に書くと、不要な変更を抑え、レビューしやすくなります。

## 一覧と確認

```bash
kanban/bin/kb list                 # done 以外
kanban/bin/kb list --all --pj kumitate
kanban/bin/kb show 204             # メタ + 本文
```

`workspace/kanban/BOARD.md` は状態が変わるたびに再生成されます。ブラウザなら [Web コンソール](console.md) のボードが同じものを表示します。
