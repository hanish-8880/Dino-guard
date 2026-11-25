"""Restricted expression evaluator for `code` evals: one Python expression, no imports, no attributes,
no dunders, no network, small whitelist of pure functions, wall-clock limit."""
from __future__ import annotations

import ast
import threading

from .sentiment import frustration

MAX_LEN = 2000
TIME_LIMIT_S = 2.0

_ALLOWED = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
    ast.Mod, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt,
    ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot, ast.IfExp, ast.Call, ast.Name, ast.Load, ast.Store,
    ast.Constant, ast.Subscript, ast.Slice, ast.List, ast.Tuple, ast.Dict, ast.Set, ast.ListComp,
    ast.GeneratorExp, ast.SetComp, ast.DictComp, ast.comprehension, ast.keyword,
)


def _count(items, value=None):
    return len(items) if value is None else sum(1 for x in items if x == value)


def _contains(text, sub):
    return str(sub).lower() in str(text).lower()


FUNCS = {"len": len, "min": min, "max": max, "sum": sum, "any": any, "all": all, "abs": abs, "round": round,
         "sorted": sorted, "int": int, "float": float, "str": str, "bool": bool, "count": _count,
         "contains": _contains, "frustration": frustration, "lower": lambda s: str(s).lower()}


class UnsafeExpression(ValueError):
    pass


def validate(expr: str) -> ast.Expression:
    if len(expr) > MAX_LEN:
        raise UnsafeExpression(f"expression longer than {MAX_LEN} chars")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise UnsafeExpression(f"syntax error: {e.msg}") from e
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise UnsafeExpression(f"{type(node).__name__} is not allowed")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise UnsafeExpression("names starting with _ are not allowed")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS:
                raise UnsafeExpression("only whitelisted functions may be called: " + ", ".join(sorted(FUNCS)))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 500:
            raise UnsafeExpression("string constant too long")
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and \
                not isinstance(node.value, bool) and abs(node.value) > 10**9:
            raise UnsafeExpression("numeric constant too large")
    return tree


def safe_eval(expr: str, env: dict):
    tree = validate(expr)
    code = compile(tree, "<eval>", "eval")
    scope = {"__builtins__": {}, **FUNCS, **{k: v for k, v in env.items() if not k.startswith("_")}}
    box: dict = {}

    def target():
        try:
            box["v"] = eval(code, scope)  # noqa: S307 - AST validated above, no builtins
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(TIME_LIMIT_S)
    if t.is_alive():
        raise TimeoutError(f"expression exceeded {TIME_LIMIT_S}s")
    if "e" in box:
        raise box["e"]
    return box.get("v")
