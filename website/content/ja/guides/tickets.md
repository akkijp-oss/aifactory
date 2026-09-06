# チケットを作る

このページで分かること: チケットの形、自由文からの起票（intake）、整った本文からの起票（kb new）、起票後の直し方。

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
| `## 完了条件` | 機械や人がチェックできる形で。「テストが緑」「PR がある」「summary.md に X が書いてある」 |

PJ と種別（workflow）は本文には書きません。kanban が `pj` / `kind` として持ちます。

## 自由文から起票する（intake）

音声の書き起こし、Slack の貼り付け、箇条書き、何でも構いません。

```bash
glue/bin/intake memo.txt            # ファイルから
cat memo.txt | glue/bin/intake -    # 標準入力から
```

intake は次を 1 回の LLM 呼び出し（judgment クラス = Fable）で行います。

1. どの PJ か、どの種別かの判定
2. 題名（50 字以内、接頭辞つき）の作成
3. 本文の整形と「完了条件」の追加

結果は `kb new` に渡され、id が返ります。本文の末尾に `（intake <日時> / model <モデル> / confidence 0.9 / <理由>）` が残るので、後から「LLM がどう判定したか」を読めます。

### 判定を先に見たいとき

```bash
glue/bin/intake memo.txt --dry-run
```

起票せず、判定結果の JSON（pj / kind / title / body / confidence / reason）だけを出します。

### PJ や種別が分かっているとき

分かっていることを LLM に推測させる必要はありません。3 つの方法で決定的に指定できます。優先順位は `--pj` / `--kind` > 本文の先頭行 > LLM。

```bash
glue/bin/intake memo.txt --pj kumitate --kind bug           # オプションで
printf 'pj: kumitate\nkind: chore\n依頼文…' | glue/bin/intake -   # 先頭 5 行以内の pj: / kind: 行で
```

指定した分は LLM の判定より優先され、LLM は整形（題名・完了条件）だけを行います。

### 判定が違っていたら

起票後に `kb set` で直します。runner は本文の `pr:` を読むので、`--pr` を変えると本文の行も書き換わります。

```bash
kanban/bin/kb set 204 --kind feature
kanban/bin/kb set 204 --pr 300
```

いつも同じ間違い方をするなら、`glue/bin/intake` 内の判定の目安（prompt 文）を直します。

## 整った本文から起票する（kb new）

チケットの形で本文を書けるなら、intake を通さず直接起票します。LLM は呼びません。

```bash
kanban/bin/kb new <pj> <kind> "<題名>" --body ticket.md
kanban/bin/kb new <pj> <kind> "<題名>" --body - <<'EOF'
本文…
## 完了条件
- …
EOF
kanban/bin/kb new kumitate merge-pr "PR #298 を develop へマージ" --pr 298 --body ticket.md
```

- `pj` は PJ 定義（`$AIFACTORY_WORKSPACE/projects/<pj>/`、無ければ `examples/projects/<pj>/`）がある PJ、`kind` は `workflow/kit/workflows/<kind>.yml` がある種別。どちらも実在を検証します
- id は連番（`MAX(id)+1`、最小 100）。DNS 名 `task-<id>.sb.internal` に使えるよう 3 桁以上です
- `--id N` で番号を指定できます（過去の記録を取り込むとき用）
- `--note` でメモ、`--status` で初期状態（既定 todo）を付けられます

## 良いチケットの書き方

| 良い | 悪い |
|---|---|
| 「calendar の表題テスト 2 件が 8 月固定のフィクスチャで時間依存。固定日時で解消して」 | 「テストが落ちるので直して」 |
| 完了条件に「rspec 全緑」「既存テストを削除・skip しない」 | 完了条件なし |
| 範囲を書く。「seeds と entrypoint だけ。モデルは変えない」 | 範囲を書かない（agent が範囲外まで直す） |
| 本番影響の有無を書く | 書かない（planner が STOP を出して止まる） |

agent は「範囲外の問題を見つけたら直さずに報告書に書く」約束で動きます（`workflow/kit/roles/_common.md`）。範囲を書けば書くほど、差分は小さく読みやすくなります。

## 一覧と確認

```bash
kanban/bin/kb list                 # done 以外
kanban/bin/kb list --all --pj kumitate
kanban/bin/kb show 204             # メタ + 本文
```

`workspace/kanban/BOARD.md` は状態が変わるたびに再生成されます。ブラウザなら [Web コンソール](console.md) のボードが同じものを表示します。
