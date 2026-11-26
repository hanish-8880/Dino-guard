"""Compare two sessions side by side, or groups of sessions (by model / harness / environment / outcome)."""
from __future__ import annotations

import sqlite3

from ..evals.runner import session_dict
from ..store.db import rows
from .stats import since_iso

METRICS = ("duration_s", "tool_calls", "denies", "instructs", "errors", "cost_usd", "tokens")
GROUP_KEYS = ("model", "harness", "environment", "outcome")


def _scores(conn, sid: str) -> dict[str, float]:
    out = {}
    for r in rows(conn, "SELECT e.name, r.score FROM eval_results r JOIN evals e ON e.id=r.eval_id WHERE "
                        "r.session_id=? ORDER BY r.created_at", (sid,)):
        if r["score"] is not None:
            out[r["name"]] = r["score"]
    return out


def _resolve(conn, sid: str) -> str:
    r = rows(conn, "SELECT id FROM sessions WHERE id=? OR id LIKE ? ORDER BY last_ts DESC LIMIT 2", (sid, sid + "%"))
    if not r:
        raise KeyError(f"no session {sid!r}")
    if len(r) > 1 and r[0]["id"] != sid:
        raise KeyError(f"session prefix {sid!r} is ambiguous")
    return r[0]["id"]


def summary(conn: sqlite3.Connection, sid: str) -> dict:
    s = session_dict(conn, _resolve(conn, sid))
    env = rows(conn, "SELECT environment FROM sessions WHERE id=?", (s["id"],))
    return {"id": s["id"], "harness": s["harness"], "model": s["model"], "environment": env[0]["environment"] if env else None,
            "first_prompt": (s["first_prompt"] or "")[:160], **{k: s[k] for k in METRICS}, "tools": s["tools"],
            "evals": _scores(conn, s["id"])}


def compare_two(conn: sqlite3.Connection, a: str, b: str) -> dict:
    sa, sb = summary(conn, a), summary(conn, b)
    delta = {}
    for k in METRICS:
        va, vb = sa[k] or 0, sb[k] or 0
        delta[k] = round(vb - va, 4)
    tools = sorted(set(sa["tools"]) | set(sb["tools"]))
    return {"a": sa, "b": sb, "delta": delta,
            "tools": [{"tool": t, "a": sa["tools"].get(t, 0), "b": sb["tools"].get(t, 0)} for t in tools]}


def outcome(s: dict) -> str:
    return "failed" if (s["errors"] or s["denies"]) else "clean"


def compare_groups(conn: sqlite3.Connection, by: str, days: int = 30, limit: int = 500) -> list[dict]:
    if by not in GROUP_KEYS:
        raise ValueError(f"--by must be one of {', '.join(GROUP_KEYS)}")
    groups: dict[str, list[dict]] = {}
    for r in rows(conn, "SELECT id, environment FROM sessions WHERE COALESCE(last_ts, started_at) >= ? "
                        "ORDER BY last_ts DESC LIMIT ?", (since_iso(days), limit)):
        s = session_dict(conn, r["id"])
        if not s:
            continue
        key = outcome(s) if by == "outcome" else (r["environment"] if by == "environment" else s.get(by))
        groups.setdefault(str(key or "unknown"), []).append(s)
    out = []
    for key, items in groups.items():
        n = len(items)
        row = {"group": key, "sessions": n}
        for k in METRICS:
            row[f"avg_{k}"] = round(sum((x[k] or 0) for x in items) / n, 4)
        row["clean_rate"] = round(sum(outcome(x) == "clean" for x in items) / n, 4)
        out.append(row)
    return sorted(out, key=lambda r: -r["sessions"])
