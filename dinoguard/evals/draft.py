"""Plain words -> code-eval expression (CLAUDE.md §17.5). LLM first; a template matcher without one."""
from __future__ import annotations

import re

from ..llm import LLMUnavailable, complete, extract_json
from .safe_eval import FUNCS, validate

FIELDS = ("cost_usd", "duration_s", "tool_calls", "denies", "instructs", "errors", "tokens", "prompts", "tools",
          "events", "first_prompt", "model", "harness", "status")

LLM_SYSTEM = (
    "You turn a plain-English success criterion for an AI coding-agent session into ONE Python expression. "
    "Available names: " + ", ".join(FIELDS) + ". tools is a dict tool->count; events is a list of dicts with keys "
    "event, tool, decision, error. Allowed functions: " + ", ".join(sorted(FUNCS)) + ". No imports, no attributes, "
    "no lambdas. The expression returns a dict {\"score\": 0..1, \"passed\": bool} or a bool. "
    "Reply as JSON {\"spec\": \"<expression>\"}.")

_NUM = r"\$?\s*(\d+(?:\.\d+)?)\s*(?:(k|m)\b)?"
_UNDER = r"(?:under|below|less than|fewer than|at most|no more than|<=?|max(?:imum)?)"
_OVER = r"(?:over|above|more than|at least|>=?|min(?:imum)?)"

# (regex, field, unit multiplier fn)
_TEMPLATES = [
    (r"cost|spend|\$", "cost_usd"),
    (r"minute|min\b", "duration_s*60"),
    (r"second|sec\b", "duration_s"),
    (r"token", "tokens"),
    (r"tool call|tool use|actions?\b|calls?\b", "tool_calls"),
    (r"error|fail", "errors"),
    (r"den(?:y|ies|ied)|block", "denies"),
    (r"instruct|tip|warning", "instructs"),
]


def _scaled(num: str, suffix: str | None) -> float:
    v = float(num)
    return v * (1000 if suffix == "k" else 1_000_000 if suffix == "m" else 1)


def template_spec(text: str) -> str | None:
    t = text.lower()
    if re.search(r"\b(no|zero|without|never)\s+(errors?|fail\w*)", t):
        return '{"score": 1.0 if errors == 0 else 0.0, "passed": errors == 0}'
    if re.search(r"\b(no|zero|without|never)\s+(den\w*|block\w*)", t):
        return '{"score": 1.0 if denies == 0 else 0.0, "passed": denies == 0}'
    for rx, field in _TEMPLATES:
        if not re.search(rx, t):
            continue
        unit = 1
        if field == "duration_s*60":
            field, unit = "duration_s", 60
        m_under = re.search(_UNDER + r"\s*" + _NUM, t)
        m_over = re.search(_OVER + r"\s*" + _NUM, t)
        m = m_under or m_over
        if not m:
            continue
        limit = _scaled(m.group(1), m.group(2)) * unit
        limit_s = repr(round(limit, 4))
        op = "<=" if m_under else ">="
        return f'{{"score": 1.0 if {field} {op} {limit_s} else 0.0, "passed": {field} {op} {limit_s}}}'
    return None


def draft_spec(text: str) -> dict:
    """Return {"spec", "method"}; raises ValueError when nothing fits."""
    try:
        obj = extract_json(complete(LLM_SYSTEM, text, timeout=30))
        spec = str(obj["spec"])
        validate(spec)
        return {"spec": spec, "method": "llm"}
    except (LLMUnavailable, ValueError, KeyError, TypeError):
        pass
    spec = template_spec(text)
    if not spec:
        raise ValueError("couldn't turn that into an eval without an LLM; try phrases like 'cost under $1', "
                         "'under 10 minutes', 'no errors', 'fewer than 50 tool calls', or pass --spec")
    validate(spec)
    return {"spec": spec, "method": "template"}
