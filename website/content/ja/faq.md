# よくある質問

導入、使い方、設定、運用について、よくある質問をまとめています。

## 全体

??? question "これは何のためのもの？"
    複数のリポジトリの開発作業を自動化するための仕組みです。人間が依頼を書き、エージェントとスクリプトが計画・実装・検証を進め、最後に人間が PR をレビューします。Dan Isler の講演で紹介された、エンジニア・エージェント・コードが役割を分担する構想をもとにしています。

??? question "git worktree でエージェントを並べるのと何が違う？"
    worktree + ターミナル多重化は「Mac 上で複数のエージェントを並べる」やり方で、DB やポートを共有するので隔離が弱く、環境の再現も手作業でした。この工場はエージェントごとに VM を 1 台与え、終わると初期状態に巻き戻します。講演の「worktree は始めるには良いが終着点ではない」に対応する次の一段です。

??? question "LLM はどこで動いている？ ワークフローの中？"
    開発作業を行う Claude Code は、VM 内で `claude -p` として起動します。ワークフローは工程や役割を定めた YAML ファイルで、Mac 上の runner がそれを読んで Claude Code を起動します。依頼を分類する intake は、Mac 上で Claude Code を 1 回呼び出します。詳しくは [LLM はどこで動くか](concepts/where-llm-runs.md) を参照してください。

??? question "コストはどのくらい？"
    1 run あたりエージェントは数分（Fable / Opus / Sonnet の混在）、ゲートは数分〜1 時間ですがトークンは消費しません。優先順位は「コストより品質」（メンテナの判断、2026-09-06）で、判断は Fable、実装は Opus にしています。安くしたいなら `routes.env` を変えます。

## 使い方

??? question "自由文で依頼するときのコツは？"
    プロジェクト名（「kumitate の」）と、何が起きて何を期待するかを書きます。範囲（「seeds だけ。モデルは変えない」）と本番影響の有無があると、エージェントが範囲外に手を出さず、planner が STOP を出さずに済みます。プロジェクトや種別が分かっているなら先頭に `pj:` / `kind:` 行を書けば LLM に推測させません。

??? question "intake の判定が違っていたら？"
    `kb set <id> --kind bug` や `--pr N` で直します。いつも同じ間違い方なら `glue/bin/intake` の判定の目安を直します。

??? question "PR ができた後、マージも自動にできる？"
    `merge-pr` チケット（`kb new <pj> merge-pr "…" --pr N`）を回すと、コンフリクト解消 → gates → review → merge まで無人で行います。マージそのものを自動にするかは人間の判断です。

??? question "同じチケットをやり直したい"
    `kb reopen <id>` で未着手に戻してから、`kb run <id>` を実行してください。同じ日に再実行する場合、前回の実行記録は `-attemptN` を付けたディレクトリに退避されます。

??? question "途中で止めたい"
    runner を実行しているターミナルで Ctrl-C を押します。VM は貸出中のまま残るため、作業を終えるなら `sandbox release <id>` で返却してください。続ける場合は `kb run <id> --resume` を実行します。

??? question "VM の中を見たい"
    `sandbox ssh <id>` で入れます。アプリは `sandbox url <id>` の URL をブラウザで開けます。run が終わると巻き戻るので、見たいなら `kb run --keep` で回します。

## 設定

??? question "エージェントにこのプロジェクトの注意点を毎回伝えたい"
    `$AIFACTORY_WORKSPACE/projects/<pj>/project.yml` の `facts` に記述します。禁止事項は `forbidden`、レビューの観点は `review_points` に書いてください。全プロジェクトに共通するルールは `workflow/kit/roles/_common.md` にまとめます。詳しくは [設定ファイルと優先順位](concepts/configuration.md) を参照してください。

??? question "モデルを変えたい"
    全体の設定は `workflow/kit/routes.env` で変更します。特定の工程だけモデルの種類を変える場合は、ワークフローの YAML に `model_class` を指定します。今回の実行だけモデルを変える場合は、環境変数 `CLAUDE_MODEL` を使います。

??? question "変更前のブランチでもゲートが失敗している"
    `project.yml` の `known_red_gates` にゲート名を書くと、runner が FAIL を INFO（参考情報）として扱うように変更し、エージェントに「直せ」と戻しません。直す PR がマージされたら消します。

??? question "新しいリポジトリを入れたい"
    [プロジェクトを追加する](guides/add-project.md)。同梱の `examples/projects/kumitate/` を `$AIFACTORY_WORKSPACE/projects/<pj>/` に写して、provision.sh、テンプレートとプール、project.yml、gates.sh、トークン、GitHub App のインストールの 6 つを行います。

## 安全

??? question "エージェントが変なコードを push しない？"
    エージェントは push しません（共通の約束）。push と PR はコード（runner）が行い、PR は人間がレビューします。ゲート（lint / test）が失敗したら PR の前に差し戻され、reviewer が「範囲外の変更」「テストを弱めた跡」を見ます。

??? question "VM から社内ネットワークに届く？"
    届きません。VM 発の接続はインターネットと sb-gw の DNS だけで、LAN・Proxmox ホスト・隣の VM・tailnet はファイアウォールで落とします（ADR-0010）。

??? question "トークンはどこにある？ 漏れない？"
    保存先は Mac の `~/.config/sandbox/` です。VM には貸出時にメモリ上のファイルシステム（tmpfs）へコピーし、初期状態に戻すときに削除します。GitHub のトークンは対象リポジトリだけで使え、1 時間で失効します。こうした制限で漏えい時の影響を抑えていますが、VM 内のプロセスからトークンを読める点には注意が必要です。詳しくは [安全対策と認証情報の管理](concepts/security.md) を参照してください。

## 運用

??? question "別のセッションと同時に作業していい？"
    同時に作業できます。ADR の番号を追加直前に確認する、他の人の変更を戻さない、貸出中の VM を変更しない、チケットの状態を `kb` で更新する、というルールを守ってください。詳細は [複数セッションで作業する](guides/multi-session.md) にまとめています。

??? question "Proxmox ホストが落ちたら？"
    ホストの電源を入れ直してください。WoL や IPMI が利用できなければ、現地で電源ボタンを操作する必要があります。プール VM は `onboot=0` のため、ホストの復旧後に `qm start` で手動起動します。

??? question "このサイトはどう更新する？"
    `website/content/ja/` を直し、`website/content/en/` に訳し、`mkdocs build --strict` が通ることを確かめて pull request にします。正本はリポジトリ側の README / ADR なので、そちらを先に直します。

??? question "自分の環境の情報（ホスト名、LAN、トークンの期限）はどこに書く？"
    workspace（`$AIFACTORY_WORKSPACE/docs/`）に置きます。リポジトリは公開なので、個人環境の事実は入れません。
