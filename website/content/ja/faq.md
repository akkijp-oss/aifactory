# FAQ

## 全体

??? question "これは何のためのもの？"
    自分が関わる複数のリポジトリの開発作業を、「依頼を書く」と「PR をレビューする」の 2 点以外は無人で回すためのものです。講演（Dan Isler "FORGET Loop Engineering. Agentic Engineering is about THIS"）の「エンジニアは冒頭のプランニングと末尾のレビューにだけ出て、間はエージェントとコードに任せろ」を実装しています。

??? question "git worktree で agent を並べるのと何が違う？"
    worktree + ターミナル多重化は「Mac 上で複数の agent を並べる」やり方で、DB やポートを共有するので隔離が弱く、環境の再現も手作業でした。この工場は agent ごとに VM を 1 台与え、終わると初期状態に巻き戻します。講演の「worktree は始めるには良いが終着点ではない」に対応する次の一段です。

??? question "LLM はどこで動いている？ workflow の中？"
    workflow では動きません。VM の中で `claude -p` として動きます。workflow は「どの step で、どの役割が、どのモデルを呼ぶか」の定義で、runner（Mac 上の Python）がそれを読んで VM に ssh して起動します。もう 1 箇所、intake が Mac 上で 1 回だけ LLM を呼びます。詳しくは [LLM はどこで動くか](concepts/where-llm-runs.md)。

??? question "コストはどのくらい？"
    1 run あたり agent は数分（Fable / Opus / Sonnet の混在）、ゲートは数分〜1 時間ですがトークンは消費しません。優先順位は「コストより品質」（メンテナの判断、2026-09-06）で、判断は Fable、実装は Opus にしています。安くしたいなら `routes.env` を変えます。

## 使い方

??? question "自由文で依頼するときのコツは？"
    PJ 名（「kumitate の」）と、何が起きて何を期待するかを書きます。範囲（「seeds だけ。モデルは変えない」）と本番影響の有無があると、agent が範囲外に手を出さず、planner が STOP を出さずに済みます。PJ や種別が分かっているなら先頭に `pj:` / `kind:` 行を書けば LLM に推測させません。

??? question "intake の判定が違っていたら？"
    `kb set <id> --kind bug` や `--pr N` で直します。いつも同じ間違い方なら `glue/bin/intake` の判定の目安を直します。

??? question "PR ができた後、マージも自動にできる？"
    `merge-pr` チケット（`kb new <pj> merge-pr "…" --pr N`）を回すと、コンフリクト解消 → gates → review → merge まで無人で行います。マージそのものを自動にするかは人間の判断です。

??? question "同じチケットをやり直したい"
    `kb reopen <id>` → `kb run <id>`。同じ日の再実行は前回の `runs/` が `-attemptN` に退避されます。

??? question "途中で止めたい"
    runner のプロセスを Ctrl-C。VM は貸出中のまま残るので `sandbox release <id>` で返すか、`kb run <id> --resume` で続けます。

??? question "VM の中を見たい"
    `sandbox ssh <id>` で入れます。アプリは `sandbox url <id>` の URL をブラウザで開けます。run が終わると巻き戻るので、見たいなら `kb run --keep` で回します。

## 設定

??? question "agent にこの PJ の注意点を毎回伝えたい"
    `$AIFACTORY_WORKSPACE/projects/<pj>/project.yml` の `facts`。禁止事項は `forbidden`、reviewer の観点は `review_points`。全 PJ 共通なら `workflow/kit/roles/_common.md`。詳しくは [設定の出どころ](concepts/configuration.md)。

??? question "モデルを変えたい"
    全体は `workflow/kit/routes.env`。1 step は workflow yml の `model_class`。1 回は環境変数 `CLAUDE_MODEL`。

??? question "ゲートが base で既に赤い"
    `project.yml` の `known_red_gates` にゲート名を書くと、runner が FAIL を INFO に格下げし、agent に「直せ」と戻しません。直す PR がマージされたら消します。

??? question "新しいリポジトリを入れたい"
    [PJ を追加する](guides/add-project.md)。同梱の `examples/projects/kumitate/` を `$AIFACTORY_WORKSPACE/projects/<pj>/` に写して、provision.sh、テンプレートとプール、project.yml、gates.sh、トークン、GitHub App の install の 6 つを行います。

## 安全

??? question "agent が変なコードを push しない？"
    agent は push しません（共通の約束）。push と PR はコード（runner）が行い、PR は人間がレビューします。ゲート（lint / test）が赤なら PR の前に差し戻され、reviewer が「範囲外の変更」「テストを弱めた跡」を見ます。

??? question "VM から社内ネットワークに届く？"
    届きません。VM 発の接続はインターネットと sb-gw の DNS だけで、LAN・Proxmox ホスト・隣の VM・tailnet は firewall で落とします（ADR-0010）。

??? question "トークンはどこにある？ 漏れない？"
    Mac の `~/.config/sandbox/` にだけあり、リポジトリには書きません。VM には take のたびに tmpfs へ注入し、巻き戻しで消えます。GitHub のトークンはそのリポジトリだけに効き 1 時間で失効します。詳しくは [安全と秘密情報](concepts/security.md)。

## 運用

??? question "別のセッションと同時に作業していい？"
    できますが約束があります。ADR 番号を取り直す、他人の変更を戻さない、貸出中の VM を触らない、`kb` で状態を更新する。[複数セッションで作業する](guides/multi-session.md)。

??? question "Proxmox ホストが落ちたら？"
    電源を入れ直します（WoL / IPMI が使えるかは環境次第。使えないと現地の物理ボタンです）。プールは onboot=0 なので `qm start` で手で起動します。

??? question "このサイトはどう更新する？"
    `website/content/ja/` を直し、`website/content/en/` に訳し、`mkdocs build --strict` が通ることを確かめて pull request にします。正本はリポジトリ側の README / ADR なので、そちらを先に直します。

??? question "自分の環境の情報（ホスト名、LAN、トークンの期限）はどこに書く？"
    workspace（`$AIFACTORY_WORKSPACE/docs/`）に置きます。リポジトリは公開なので、個人環境の事実は入れません。
