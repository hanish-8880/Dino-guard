"""Build a session trace: ordered + nested items and an execution graph with sub-agents."""
from __future__ import annotations

import sqlite3

from ..store.db import one, rows
from ..store.writer import load_json


def _agent_msg(d: dict) -> str:
    """The exact text the agent received for a decision (mirrors the Claude adapter prefix)."""
    if d["decision"] == "deny":
        return f"[DinoGuard:{d['policy']}] {d['reason'] or ''}"
    if d["decision"] == "instruct":
        return f"[DinoGuard] {d['reason'] or ''}"
    return ""


def build_trace(conn: sqlite3.Connection, session_id: str) -> dict:
    sess = one(conn, "SELECT * FROM sessions WHERE id=?", (session_id,))
    evs = rows(conn, "SELECT * FROM events WHERE session_id=? ORDER BY ts, rowid", (session_id,))
    ids = [e["id"] for e in evs]
    decs: dict[str, list] = {}
    if ids:
        q = ",".join("?" * len(ids))
        for d in rows(conn, f"SELECT * FROM decisions WHERE event_id IN ({q})", ids):
            d["reviewer_verdict"] = load_json(d.get("reviewer_verdict"))
            d["agent_message"] = _agent_msg(d) if d["enforced"] else ""
            decs.setdefault(d["event_id"], []).append(d)
    calls = rows(conn, "SELECT * FROM model_calls WHERE session_id=? ORDER BY ts", (session_id,))
    approvals = rows(conn, f"SELECT * FROM approvals WHERE event_id IN ({','.join('?' * len(ids))})", ids) if ids else []
    cps = {c["event_id"]: c for c in rows(conn, "SELECT * FROM checkpoints WHERE session_id=?", (session_id,))}

    items, by_id = [], {}
    for e in evs:
        it = {"type": "hook", "id": e["id"], "ts": e["ts"], "event": e["event"], "tool": e["tool_name"],
              "tool_class": e["tool_class"], "input": load_json(e["input_json"], {}), "output": e["output_excerpt"],
              "error": e["error"], "duration_ms": e["duration_ms"], "decision": e["decision"],
              "latency_ms": e["latency_ms"], "agent_id": e["agent_id"], "parent_id": e["parent_id"],
              "decisions": decs.get(e["id"], []), "checkpoint": cps.get(e["id"]), "children": []}
        by_id[e["id"]] = it
        if e["parent_id"] and e["parent_id"] in by_id:
            by_id[e["parent_id"]]["children"].append(it)
        else:
            items.append(it)
    for c in calls:
        items.append({"type": "model", "id": c["id"], "ts": c["ts"], "model": c["model"],
                      "tokens_in": c["tokens_in"], "tokens_out": c["tokens_out"], "cache_read": c["cache_read"],
                      "cost_usd": c["cost_usd"], "children": []})
    for a in approvals:
        items.append({"type": "human", "id": a["id"], "ts": a["answered_at"] or a["sent_at"],
                      "summary": a["summary"], "answer": a["answer"] or "pending", "children": []})
    items.sort(key=lambda i: i["ts"] or "")

    blocked = [{"event_id": e["id"], "ts": e["ts"], "tool": e["tool_name"], "policy": d["policy"],
                "decision": d["decision"], "message": d["agent_message"], "input": load_json(e["input_json"], {})}
               for e in evs for d in decs.get(e["id"], []) if d["enforced"] and d["decision"] != "allow"]
    return {"session": sess, "items": items, "graph": graph(evs), "blocked": blocked,
            "counts": {"events": len(evs), "model_calls": len(calls)}}


def graph(evs: list[dict]) -> dict:
    """Nodes: the main agent + each sub-agent (agent_id); edges: Task tool / SubagentStart -> sub-agent."""
    nodes = {"main": {"id": "main", "label": "main agent", "tools": 0, "denies": 0, "first": None}}
    edges = []
    pending_tasks: list[str] = []
    for e in evs:
        aid = e["agent_id"] or "main"
        if aid not in nodes:
            parent = "main"
            nodes[aid] = {"id": aid, "label": f"sub-agent {aid[:8]}", "tools": 0, "denies": 0, "first": e["ts"]}
            edges.append({"from": parent, "to": aid, "via": pending_tasks.pop(0) if pending_tasks else "SubagentStart"})
        n = nodes[aid]
        n["first"] = n["first"] or e["ts"]
        if e["event"] == "PreToolUse":
            n["tools"] += 1
            if e["decision"] == "deny":
                n["denies"] += 1
            if e["tool_name"] == "Task":
                inp = load_json(e["input_json"], {})
                pending_tasks.append(str(inp.get("description") or inp.get("subagent_type") or "Task"))
    return {"nodes": list(nodes.values()), "edges": edges}
