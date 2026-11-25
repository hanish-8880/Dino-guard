"""Parse "tell me when ..." into an alert trigger spec (rule-based; LLM fallback when configured)."""
from __future__ import annotations

import json
import re

METRICS = {
    "denies": ["deny", "denies", "denied", "blocked", "blocks"],
    "instructs": ["instruct", "instructs", "instructed", "warnings", "tips"],
    "errors": ["error", "errors", "failures", "failed", "failing"],
    "cost_usd": ["cost", "spend", "spent", "dollars", "$", "usd"],
    "tokens": ["tokens", "token"],
    "hook_latency_p95_ms": ["latency", "p95", "slow"],
    "events": ["events", "actions", "tool calls", "calls"],
    "sessions": ["sessions"],
    "approvals_timeout": ["approval timeouts", "unanswered approvals"],
}
OPS = [(r">=|at least|no less than", ">="), (r"<=|at most|no more than", "<="),
       (r">|more than|over|above|exceeds?|greater than", ">"), (r"<|less than|below|under|drops? below", "<"),
       (r"==|=|equals?|is exactly", "==")]
_NUM = r"\$?(-?\d+(?:\.\d+)?)\s*(k|m)?"


def _op(text: str) -> tuple[str, int, int] | None:
    best = None
    for rx, op in OPS:
        for m in re.finditer(rf"(?i)\s*(?:{rx})\s*", text):
            if best is None or m.start() < best[1]:
                best = (op, m.start(), m.end())
    return best


def _num(s: str) -> float | None:
    m = re.search(_NUM, s, re.I)
    if not m:
        return None
    v = float(m.group(1))
    if m.group(2):
        v *= 1000 if m.group(2).lower() == "k" else 1_000_000
    return v


def _window(text: str, default: int = 3600) -> int:
    m = re.search(r"(?i)\b(?:in|within|over|per|every|last)\s+(?:the\s+)?(?:last\s+)?(\d+)?\s*"
                  r"(s|sec|seconds?|m|mins?|minutes?|h|hrs?|hours?|d|days?|weeks?)\b", text)
    if not m:
        return default
    n = int(m.group(1) or 1)
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2).lower()[0]]


def parse(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"(?i)^(tell|alert|notify|ping|message)\s+me\s+(when|if)\s+", "", t)
    if not t:
        raise ValueError("describe the condition, e.g. 'denies > 5 in 10 minutes'")
    if t.lower().startswith("sql:"):
        body = t[4:].strip()
        m = re.search(r"\s*(>=|<=|>|<|==)\s*(-?\d+(?:\.\d+)?)\s*$", body)
        if not m:
            raise ValueError("sql alerts need a comparison at the end, e.g. 'sql: SELECT COUNT(*) ... > 10'")
        return {"trigger_kind": "sql", "sql": body[: m.start()].strip(), "op": m.group(1), "value": float(m.group(2)),
                "window_s": 0}
    o = _op(t)
    if not o:
        return _llm(t)
    op, s, e = o
    left, right = t[:s].lower(), t[e:]
    value = _num(right)
    if value is None:
        raise ValueError("could not find the threshold number")
    window = _window(right)
    m = re.search(r"(?i)\b([\w-]+)\s+(?:eval\s+)?score\b", left)
    if m and "eval" not in m.group(1):
        return {"trigger_kind": "eval_score", "eval": m.group(1), "op": op, "value": value, "window_s": window or 86400}
    for metric, words in METRICS.items():
        if any(w in left for w in words):
            return {"trigger_kind": "errors" if metric == "errors" else "metric", "metric": metric, "op": op,
                    "value": value, "window_s": window}
    return _llm(t)


def _llm(text: str) -> dict:
    from ..llm import LLMUnavailable, complete_json
    try:
        spec = complete_json(
            "Turn the user's alert request into JSON with keys trigger_kind (metric|errors|eval_score|sql), "
            f"metric (one of {sorted(METRICS)}), op (> >= < <= ==), value (number), window_s (seconds). "
            "Only output JSON.", text)
    except LLMUnavailable as e:
        raise ValueError(f"could not understand {text!r}; try 'denies > 5 in 10 minutes' ({e})") from e
    if not isinstance(spec, dict) or spec.get("op") not in (">", ">=", "<", "<=", "=="):
        raise ValueError(f"LLM returned an unusable spec: {json.dumps(spec)[:200]}")
    return spec
