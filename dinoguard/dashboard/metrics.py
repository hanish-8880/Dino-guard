"""Aggregates for Home + Metrics pages."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from ..store.db import rows


def pct(values: list[float], p: float) -> float | None:
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


def _since(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")


def latency(values) -> dict:
    vals = [x for x in values if x is not None]
    return {"n": len(vals), "p50": pct(vals, .5), "p95": pct(vals, .95), "p99": pct(vals, .99)}


def home(conn: sqlite3.Connection) -> dict:
    since = _since(24)
    d = rows(conn, "SELECT decision, COUNT(*) n FROM events WHERE ts >= ? AND event='PreToolUse' GROUP BY decision",
             (since,))
    by = {r["decision"]: r["n"] for r in d}
    cost = rows(conn, "SELECT COALESCE(SUM(cost_usd),0) c, COALESCE(SUM(tokens_in+tokens_out+cache_read+cache_write),0) t"
                      " FROM model_calls WHERE ts >= ?", (since,))[0]
    days = rows(conn, "SELECT substr(ts,1,10) day, SUM(decision='allow') allow, SUM(decision='instruct') instruct,"
                      " SUM(decision='deny') deny FROM events WHERE event='PreToolUse' AND ts >= ?"
                      " GROUP BY day ORDER BY day", (_since(24 * 14),))
    top = rows(conn, "SELECT d.policy policy, d.decision decision, COUNT(*) n FROM decisions d JOIN events e "
                     "ON e.id=d.event_id WHERE e.ts >= ? AND d.decision != 'allow' GROUP BY d.policy, d.decision "
                     "ORDER BY n DESC LIMIT 8",
               (_since(24 * 7),))
    insights = rows(conn, "SELECT COUNT(*) n FROM insights WHERE status != 'resolved'")[0]["n"]
    sessions = rows(conn, "SELECT COUNT(*) n FROM sessions WHERE last_ts >= ?", (since,))[0]["n"]
    return {"actions": sum(by.values()), "allowed": by.get("allow", 0), "instructed": by.get("instruct", 0),
            "denied": by.get("deny", 0), "cost_usd": round(cost["c"], 4), "tokens": cost["t"],
            "open_insights": insights, "sessions": sessions, "days": days, "top_policies": top}


def metrics(conn: sqlite3.Connection, hours: int = 24 * 7) -> dict:
    since = _since(hours)
    hook_lat = [r["latency_ms"] for r in rows(conn, "SELECT latency_ms FROM events WHERE ts >= ?", (since,))]
    tools: dict[str, list] = {}
    for r in rows(conn, "SELECT tool_name, duration_ms FROM events WHERE ts >= ? AND event LIKE 'PostToolUse%' "
                        "AND duration_ms IS NOT NULL", (since,)):
        tools.setdefault(r["tool_name"] or "?", []).append(r["duration_ms"])
    models: dict[str, list] = {}
    for r in rows(conn, "SELECT model, latency_ms FROM model_calls WHERE ts >= ?", (since,)):
        models.setdefault(r["model"] or "?", []).append(r["latency_ms"])
    cost = rows(conn, "SELECT model, COUNT(*) calls, SUM(cost_usd) cost, SUM(tokens_in) tin, SUM(tokens_out) tout,"
                      " SUM(cache_read) cr FROM model_calls WHERE ts >= ? GROUP BY model ORDER BY cost DESC", (since,))
    ctx = rows(conn, "SELECT session_id, MAX(context_tokens) ctx FROM model_calls WHERE ts >= ? GROUP BY session_id "
                     "ORDER BY ctx DESC LIMIT 20", (since,))
    err = rows(conn, "SELECT tool_name, COUNT(*) n, SUM(error IS NOT NULL) errors FROM events WHERE ts >= ? AND "
                     "event LIKE 'PostToolUse%' GROUP BY tool_name ORDER BY n DESC", (since,))
    return {
        "hours": hours,
        "hook_latency_ms": latency(hook_lat),
        "tool_latency_ms": {k: latency(v) for k, v in sorted(tools.items())},
        "model_latency_ms": {k: latency(v) for k, v in sorted(models.items())},
        "cost_per_model": cost, "context_usage": ctx,
        "error_rates": [{**r, "rate": round((r["errors"] or 0) / r["n"], 4) if r["n"] else 0} for r in err],
    }
