"""lib/aifactory_workflow.py: workflow 定義から「実効モデル」と「次の行き先」を解く純関数の正本。

runner（workflow/bin/run）と console（console/lib/core.py）が同じ規則を使う。設定画面が同じ答えを
出すために式を写すと、写しが増えた分だけ実態と食い違う（ADR-0062）。ここは状態を持たない:
戻せる回数の数え上げ（state.json の loops）と severity の加点（ADR-0053）は runner に残す。

- ROLE_CLASS: 役割 → 既定のモデルクラス（routes.env 冒頭のコメントと kit/roles/<role>.md の「クラス:」は説明で、正本はここ）
- resolve_model(step, routes, env_model): 実効モデルと、どこから決まったか
- token_family(model): モデル名 → 鍵の系統（runner が VM の鍵を選ぶのと、設定画面が「その鍵があるか」を言うのに同じ答えを使う）
- transition_of(step, ok): 合否ごとの行き先と、どのキーから決まったか
"""
import re

ROLE_CLASS = {"planner": "judgment", "reviewer": "judgment", "researcher": "research", "implementer": "coding"}

MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def route_keys():
    """routes.env で意味のあるキー（クラスごとの経路 + 既定）。設定を書くときの許可リストはここから作る"""
    return sorted({f"MODEL_{c}" for c in ROLE_CLASS.values()} | {"MODEL_default"})


def token_family(model):
    """モデル名 → 鍵の系統（FABLE / OPUS / SONNET / HAIKU）。分からなければ空。
    take が鍵プールから用途ごとに選んだ鍵は VM の /run/sandbox/env に CLAUDE_CODE_OAUTH_TOKEN_<系統> で入っている（ADR-0044 / ADR-0060）"""
    m = re.search(r"(fable|opus|sonnet|haiku)", str(model).lower())
    return m.group(1).upper() if m else ""


def resolve_model(step, routes, env_model=None):
    """step 1 つの実効モデル。code 工程（role が無い）は None（モデルを使わない）。

    優先順: env_model（run 起動時の CLAUDE_MODEL）> step の model > MODEL_<クラス> > MODEL_default。
    クラスは step の model_class があればそれ、無ければ役割の既定。未知の役割は例外にせず
    model_class=None で返す（呼ぶ側が「解決できません」と言えるように）。

    step の model は「同じクラスの他の工程を動かさずに、この工程だけ別のモデルにする」ための上書き
    （model_class では同じクラスの全工程が道連れになる。チケット 416 / ADR-0063）。
    """
    step = step or {}
    role = step.get("role")
    if not role:
        return None
    cls = step.get("model_class") or ROLE_CLASS.get(role)
    r = {"role": role, "model_class": cls, "class_from": ("step" if step.get("model_class") else "role") if cls else None,
         "route_key": f"MODEL_{cls}" if cls else None, "model": None, "model_from": None}
    if not cls:
        return r
    routes = routes or {}
    if env_model:
        r["model"], r["model_from"] = env_model, "env"
    elif step.get("model"):
        r["model"], r["model_from"] = step["model"], "step"
    elif routes.get(r["route_key"]):
        r["model"], r["model_from"] = routes[r["route_key"]], "routes"
    elif routes.get("MODEL_default"):
        r["model"], r["model_from"] = routes["MODEL_default"], "default"
    return r


def target_kind(to):
    """遷移先の種類。'human' は人間待ちで終わり、'end' は正常終了、それ以外は step の id"""
    return to if to in ("human", "end") else ("step" if to else None)


def transition_of(step, ok):
    """step の結果（ok=True/False）に対する行き先。workflow/bin/run の transition() と同じ規則。

    on_pass / on_fail のどちらかが書いてあればその組で決め、無ければ成功は next・失敗は人間待ち。
    値が無い（None）ときは成功は正常終了・失敗は人間待ち。max_loops / else は戻る形（{goto: ...}）のときだけ入る。
    """
    step = step or {}
    if "on_pass" in step or "on_fail" in step:
        t, source = step.get("on_pass" if ok else "on_fail"), ("on_pass" if ok else "on_fail")
    else:
        t, source = (step.get("next"), "next") if ok else ("human", "default")
    out = {"to": None, "kind": None, "max_loops": None, "else": None, "else_kind": None, "source": source}
    if t is None:
        out.update(to=("end" if ok else "human"), source="default")
    elif isinstance(t, str):
        out.update(to=t)
    else:
        out.update(to=t.get("goto"), max_loops=t.get("max_loops", 1), **{"else": t.get("else", "human")})
        out["else_kind"] = target_kind(out["else"])
    out["kind"] = target_kind(out["to"])
    return out


def main_path(steps):
    """最初の step から成功の遷移だけを辿った工程の並び。ここに載らない step は失敗したときだけ回る。

    end / human / 未知の行き先で止め、巡回（同じ step に戻る）でも止める（無限に伸ばさない）。
    """
    steps = steps or []
    by_id = {s.get("id"): s for s in steps if isinstance(s, dict) and s.get("id")}
    cur, path = (steps[0].get("id") if steps and isinstance(steps[0], dict) else None), []
    while cur and cur in by_id and cur not in path:
        path.append(cur)
        cur = transition_of(by_id[cur], True)["to"]
    return path
