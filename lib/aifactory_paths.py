"""lib/aifactory_paths.py: aifactory の「置き場」の正本。枠組み（このリポジトリ）と運用データ（workspace）を分ける（ADR-0016）。

kb / run / intake / dispatch / console は全部ここを読む。置き場の判断はここに 1 つ。

- AIFACTORY_WORKSPACE: 運用データの根。既定は <repo>/workspace/（git 追跡外）
    workspace/
    ├── projects/<pj>/{project.yml,provision.sh,gates.sh}   PJ 定義（無ければ examples/projects/<pj>/ を探す）
    ├── kanban/{kanban.db,tickets/,BOARD.md}                チケット台帳
    ├── runs/<run>/                                         実行記録
    ├── logs/{intake.log,dispatch.log}                      取り込み・配車の記録
    └── docs/                                               私有のメモ（インフラ台帳など。枠組みは読まない）
- examples/projects/<pj>/: 同梱のサンプル PJ 定義。workspace に同名の PJ があればそちらが勝つ
- KB_ROOT / CONSOLE_JOBS: 従来どおり個別に上書きできる（テスト用）
- 互換（移行中だけ）: AIFACTORY_WORKSPACE が未設定で <repo>/kanban/kanban.db が残っている間は旧配置
  （sandbox/templates/ kanban/ workflow/runs/ glue/*.log）をそのまま使う。bin/migrate-workspace.sh で移すと新配置に切り替わる
"""
import os, pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples" / "projects"

_env = os.environ.get("AIFACTORY_WORKSPACE")
LEGACY = not _env and (REPO / "kanban" / "kanban.db").exists()
WORKSPACE = pathlib.Path(_env).expanduser() if _env else REPO / "workspace"

if LEGACY:
    PROJECT_DIRS = [REPO / "sandbox" / "templates", EXAMPLES]
    _kb, RUNS, LOGS = REPO / "kanban", REPO / "workflow" / "runs", REPO / "glue"
else:
    PROJECT_DIRS = [WORKSPACE / "projects", EXAMPLES]
    _kb, RUNS, LOGS = WORKSPACE / "kanban", WORKSPACE / "runs", WORKSPACE / "logs"
KB_ROOT = pathlib.Path(os.environ.get("KB_ROOT") or _kb).expanduser()
JOBS = pathlib.Path(os.environ.get("CONSOLE_JOBS") or (REPO / "console" / "jobs")).expanduser()


def _is_project(d):
    return d.is_dir() and not d.name.startswith((".", "_")) and ((d / "project.yml").exists() or (d / "provision.sh").exists())


def project_dir(pj):
    """PJ 定義のディレクトリ（先に見つかった置き場）。無ければ None"""
    for root in PROJECT_DIRS:
        d = root / pj
        if _is_project(d): return d
    return None


def projects():
    """PJ 名の一覧（全置き場の和。重複は先勝ち）"""
    out = []
    for root in PROJECT_DIRS:
        if not root.is_dir(): continue
        for d in sorted(root.iterdir()):
            if _is_project(d) and d.name not in out: out.append(d.name)
    return sorted(out)


def run_path(run):
    """kanban の run 列（run 名。旧記録は workflow/runs/<名前>）→ 実行記録ディレクトリ"""
    return RUNS / pathlib.Path(str(run)).name


def describe():
    return {"workspace": str(WORKSPACE), "legacy": LEGACY, "project_dirs": [str(p) for p in PROJECT_DIRS],
            "kb_root": str(KB_ROOT), "runs": str(RUNS), "logs": str(LOGS), "jobs": str(JOBS)}


if __name__ == "__main__":
    import json
    print(json.dumps(describe(), ensure_ascii=False, indent=2))
