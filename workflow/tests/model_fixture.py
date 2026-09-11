"""実効モデルの検査で runner（workflow/tests）と console（console/tests）が共有する fixture（チケット 416）。

規則の正本は lib/aifactory_workflow.py で、ここは「この定義ならこの答え」という期待表だけを持つ。
両方のテストが同じ表を読むので、片方だけが通る（＝画面と runner で答えが違う）状態を検査で拾える。

- ROUTES: 経路表（routes.env と同じ形）
- STEPS: 工程の定義。工程単位の model / model_class・共通経路・既定・継承（上書き無し）の組合せを 1 つずつ持つ
- CASES: (step_id, env_model) → 期待する {model, model_from, model_class, class_from}
"""

ROUTES = {"MODEL_judgment": "claude-fable-5-1", "MODEL_research": "claude-sonnet-5",
          "MODEL_coding": "claude-opus-5", "MODEL_default": "claude-opus-5"}

ROUTES_DEFAULT_ONLY = {"MODEL_default": "claude-opus-5"}

STEPS = [
    {"id": "research", "role": "researcher", "next": "design"},
    {"id": "design", "role": "planner", "next": "implement"},
    {"id": "implement", "role": "implementer", "next": "gates"},
    {"id": "review", "role": "reviewer", "on_pass": "sync", "on_fail": {"goto": "implement", "max_loops": 1, "else": "human"}},
    {"id": "slow", "role": "researcher", "model_class": "coding", "next": "end"},
    {"id": "pinned", "role": "planner", "model": "claude-haiku-4-5-20251001", "next": "end"},
    {"id": "pinned_class", "role": "researcher", "model_class": "coding", "model": "claude-sonnet-5", "next": "end"},
    {"id": "gates", "code": "gates.sh", "on_pass": "review", "on_fail": {"goto": "implement", "max_loops": 2, "else": "human"}},
    {"id": "pr", "code": "pr-create.sh", "next": "automerge"},
    {"id": "automerge", "code": "pr-automerge.sh", "on_pass": "end", "on_fail": "human"},
    {"id": "last", "role": "planner"},
]

# (step_id, env_model, routes) → 期待値。routes が None なら ROUTES
CASES = [
    # 継承（工程に上書き無し）: 役割の既定クラス → 共通経路
    ("research", None, None, {"model": "claude-sonnet-5", "model_from": "routes", "model_class": "research", "class_from": "role"}),
    ("design", None, None, {"model": "claude-fable-5-1", "model_from": "routes", "model_class": "judgment", "class_from": "role"}),
    ("implement", None, None, {"model": "claude-opus-5", "model_from": "routes", "model_class": "coding", "class_from": "role"}),
    ("review", None, None, {"model": "claude-fable-5-1", "model_from": "routes", "model_class": "judgment", "class_from": "role"}),
    # 工程のクラス上書き: 同じ役割でも別のクラスの経路を読む
    ("slow", None, None, {"model": "claude-opus-5", "model_from": "routes", "model_class": "coding", "class_from": "step"}),
    # 工程のモデル上書き: 共通経路より優先し、クラスの表示は役割の既定のまま
    ("pinned", None, None, {"model": "claude-haiku-4-5-20251001", "model_from": "step", "model_class": "judgment", "class_from": "role"}),
    # クラスとモデルの両方: モデルが勝つ（クラスは表示のためだけに残る）
    ("pinned_class", None, None, {"model": "claude-sonnet-5", "model_from": "step", "model_class": "coding", "class_from": "step"}),
    # 既定への落ち: クラスの行が無ければ MODEL_default
    ("design", None, ROUTES_DEFAULT_ONLY, {"model": "claude-opus-5", "model_from": "default", "model_class": "judgment", "class_from": "role"}),
    ("research", None, ROUTES_DEFAULT_ONLY, {"model": "claude-opus-5", "model_from": "default", "model_class": "research", "class_from": "role"}),
    # 環境変数の上書き: 工程の model より強い（run を起こすときの CLAUDE_MODEL）
    ("design", "claude-from-env", None, {"model": "claude-from-env", "model_from": "env", "model_class": "judgment", "class_from": "role"}),
    ("pinned", "claude-from-env", None, {"model": "claude-from-env", "model_from": "env", "model_class": "judgment", "class_from": "role"}),
    ("slow", "claude-from-env", None, {"model": "claude-from-env", "model_from": "env", "model_class": "coding", "class_from": "step"}),
]

# 系統（鍵プールの用途を選ぶ規則）。空文字は「分からない」
FAMILIES = [("claude-fable-5-1", "FABLE"), ("claude-opus-5", "OPUS"), ("claude-sonnet-5", "SONNET"),
            ("claude-haiku-4-5-20251001", "HAIKU"), ("gpt-something", ""), ("", "")]


def step(sid):
    return next(s for s in STEPS if s["id"] == sid)


def routes_of(case_routes):
    return dict(ROUTES if case_routes is None else case_routes)
