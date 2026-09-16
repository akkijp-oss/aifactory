# ADR 0084: 票の参照は「外部 issue（取得可否つき）」と「内部の票番号」の 2 列に分ける。読めない参照は役割文書が名指しで断る

- 状態: Accepted
- 日付: 2026-09-16

## 状況

チケット本文には `https://github.com/akkijp/kumitate/issues/393` の形の URL が自由文として書かれてきた。
ところがこの番号は **aifactory の内部票番号**で、GitHub 上に同じ番号の issue は無い。さらに sandbox に注入する
トークンは issues へ **403（`Resource not accessible by integration`）** を返す。

つまり run から見ると、**取りに行けるように見えて絶対に取れない参照**が本文に埋まっている。

2026-09-14 の run（`2026-09-14-kumitate-521`）で researcher が 4 回連続で失敗した:

```
gh issue view 393 --repo akkijp/kumitate        → Could not resolve to an issue or pull request
gh api repos/akkijp/kumitate/issues/393         → 403 Resource not accessible by integration
gh api repos/akkijp/kumitate/issues/520         → 404
gh api repos/akkijp/kumitate/issues/393/comments → 403
```

約 20 秒とトークンを焼いて、得たものはゼロだった。**エージェントは賢く振る舞っている**——「repo へのアクセス自体は
効く」ことを確かめたうえで「#393 は GitHub issue として存在しないらしい」と自力で正しく推論している。
誤解を招いていたのは構造の方である。

管理役はその後 5 件の票の読み替え表に「GitHub の issue は読めない。票本文と実コードだけで進めること」と
**手で書き足し**、その 3 run は API 呼び出しがゼロになった。効果は実測できているが、手作業は次の票でまた要る。

## 決定

1. **参照は 2 列に分ける。** `tickets` に `related_issue`（外部 issue の URL）と `related_ticket`（aifactory の
   内部票番号）を足す。どちらも `TEXT` の単純列で、既存の列と同じ形にする（正規化した別テーブルは作らない）。
   **どちらの参照なのかを、読む側が文字列から推し量らずに済む**ことがこの列の目的なので、内部番号を
   `--issue` に書くこと・URL を `--ticket` に書くことは、書く側で断る。

2. **外部 issue には取得可否のフラグを付ける。** `related_issue_access` は `readable` / `unreadable` の 2 語だけ
   （自由文にしない）。**書かなければ `unreadable` に倒す。** sandbox のトークンは issues に 403 を返すのが
   既定の姿なので、「確かめていない参照は取りに行かない」が安全側である。ADR-0077 決定 5（書けていない・
   確かめられないものは安全側に倒す）と同じ判断で、倒す先が逆になるだけである。

3. **本文の自由文は解釈しない。** 本文の URL を機械が拾って列に写すことはしない（誤検知と取りこぼしの両方が
   出る。ADR-0077 決定 2 と同じ理由）。既存票の移行もしない——**新しい起票から構造化する**。

4. **「issues は読めない」は researcher の役割文書に 1 行で書く。** 依頼文は役割文書をそのまま貼るので
   （`workflow/bin/run` の `build_prompt`）、文面を runner 側に書き写さない。正本は
   `workflow/kit/roles/researcher.md` の 1 行だけで、読み替え表への手書きはこれで要らなくなる。

5. **トークンの権限は広げない。** 「issues を読めるようにする」は権限を広げる方向の別の判断であり、
   まず「読めない前提で正しく振る舞う」を満たす。feedback-assets の画像取得も同じ扱いで、本 ADR の外。

## 結果

- 起票の口（`kb new` / `kb set`）に `--issue` / `--issue-access` / `--ticket` が増える。書かない票の挙動は
  変わらず、列が無かった頃の `kanban.db` には `kb` が起動時に足す（ADR-0077 と同じ `ADDED_COLUMNS` の機構）。
- 列は `kb show` / `kb history` と、`SELECT *` で票を返す MCP / HTTP API（`ticket_show` / `ticket_list`）から
  そのまま読める。
- **依頼文にはまだ列が出ない。** runner が VM に運ぶのは本文（`ticket.md`）だけなので、この版で run の
  振る舞いを変えるのは決定 4 の 1 行である。列を依頼文に出すかは、kb から runner へメタを渡す口の話になるので
  別票にする（今の効果は「人が読み替え表を手書きしなくてよくなる」ところまで）。
- 実 run での「issue 取得の試行が 0 回」は VM の中からは確かめられない（run の中から別の run は起こせない）。
  #552 で確立した分担どおり、実データでの確認は管理役が ctl 側で行う。
