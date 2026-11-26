"""Tool and policy analytics."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from ..dashboard.metrics import pct
from ..store.db import rows


def since_iso(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def tools(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    since = since_iso(days)
    acc: dict[str, dict] = {}
    for r in rows(conn, "SELECT tool_name, event, decision, error, duration_ms FROM events WHERE ts >= ? AND "
                        "tool_name IS NOT NULL", (since,)):
        a = acc.setdefault(r["tool_name"], {"tool": r["tool_name"], "calls": 0, "denied": 0, "results": 0,
                                            "errors": 0, "durations": []})
        if r["event"] == "PreToolUse":
            a["calls"] += 1
            a["denied"] += r["decision"] == "deny"
        elif (r["event"] or "").startswith("PostToolUse"):
            a["results"] += 1
            a["errors"] += bool(r["error"])
            if r["duration_ms"] is not None:
                a["durations"].append(r["duration_ms"])
    out = []
    for a in acc.values():
        d = a.pop("durations")
        a["error_rate"] = round(a["errors"] / a["results"], 4) if a["results"] else 0.0
        a["p50_ms"], a["p95_ms"] = pct(d, .5), pct(d, .95)
        out.append(a)
    return sorted(out, key=lambda a: -a["calls"])


def policies(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    """allow/instruct/deny counts that were enforced, plus would-* counts from observe mode."""
    acc: dict[str, dict] = {}
    for r in rows(conn, "SELECT d.policy, d.decision, d.mode, d.enforced, COUNT(*) n FROM decisions d JOIN events e "
                        "ON e.id=d.event_id WHERE e.ts >= ? GROUP BY d.policy, d.decision, d.mode, d.enforced",
                  (since_iso(days),)):
        a = acc.setdefault(r["policy"], {"policy": r["policy"], "allow": 0, "instruct": 0, "deny": 0,
                                         "would_instruct": 0, "would_deny": 0, "errors": 0})
        kind = r["decision"]
        if kind not in ("allow", "instruct", "deny"):
            continue
        if r["mode"] == "observe" and kind != "allow":
            a[f"would_{kind}"] += r["n"]
        else:
            a[kind] += r["n"]
    for r in rows(conn, "SELECT d.policy, COUNT(*) n FROM decisions d JOIN events e ON e.id=d.event_id WHERE "
                        "e.ts >= ? AND d.error IS NOT NULL GROUP BY d.policy", (since_iso(days),)):
        acc.setdefault(r["policy"], {"policy": r["policy"], "allow": 0, "instruct": 0, "deny": 0,
                                     "would_instruct": 0, "would_deny": 0, "errors": 0})["errors"] = r["n"]
    return sorted(acc.values(), key=lambda a: -(a["deny"] + a["instruct"] + a["would_deny"] + a["would_instruct"]))
