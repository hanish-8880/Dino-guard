"""`dinoguard mcp`: minimal MCP server over stdio (JSON-RPC 2.0, newline-delimited), read-only tools."""
from __future__ import annotations

import json
import sys

from ..version import __version__

PROTOCOL = "2025-06-18"

TOOLS = [
    {"name": "list_sessions", "description": "Recent agent sessions recorded by DinoGuard.",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}}},
    {"name": "get_trace", "description": "Ordered trace of one session: tool calls, decisions, model calls.",
     "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]}},
    {"name": "query", "description": "Run a read-only SQL SELECT over the DinoGuard store (5 s timeout).",
     "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}},
    {"name": "list_findings", "description": "Audit findings (evidence for review, not proof).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "explain_decision", "description": "Why DinoGuard allowed/instructed/denied one event.",
     "inputSchema": {"type": "object", "properties": {"event_id": {"type": "string"}}, "required": ["event_id"]}},
]


def call_tool(conn, name: str, args: dict):
    from ..store.db import rows
    from ..store.queries import safe_select
    if name == "list_sessions":
        return rows(conn, "SELECT id, harness, cwd, first_prompt, status, last_ts, cost_usd FROM sessions "
                          "ORDER BY last_ts DESC LIMIT ?", (min(int(args.get("limit", 20)), 200),))
    if name == "get_trace":
        from ..dashboard.trace import build_trace
        t = build_trace(conn, str(args["session_id"]))
        return {"session": t["session"], "blocked": t["blocked"], "graph": t["graph"],
                "items": [{k: i.get(k) for k in ("type", "ts", "event", "tool", "decision", "input", "error", "model")}
                          for i in t["items"][:300]]}
    if name == "query":
        cols, out = safe_select(conn, str(args["sql"]), timeout_s=5.0, max_rows=200)
        return {"columns": cols, "rows": out}
    if name == "list_findings":
        return rows(conn, "SELECT id, failure_mode, severity, count, session_ids FROM findings ORDER BY created_at DESC LIMIT 100")
    if name == "explain_decision":
        ev = rows(conn, "SELECT id, session_id, ts, event, tool_name, input_json, decision FROM events WHERE id=?",
                  (str(args["event_id"]),))
        if not ev:
            raise KeyError("no such event")
        decs = rows(conn, "SELECT policy, mode, decision, reason, authority, enforced, reviewer_verdict FROM decisions "
                          "WHERE event_id=?", (ev[0]["id"],))
        return {"event": ev[0], "final": ev[0]["decision"], "policies": decs,
                "note": "Only non-allow results are stored; an empty list means every policy allowed it."}
    raise KeyError(f"unknown tool {name}")


def handle(conn, msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:
        return None   # notification
    try:
        if method == "initialize":
            result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "dinoguard", "version": __version__}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params") or {}
            try:
                data = call_tool(conn, p.get("name"), p.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(data, default=str)}], "isError": False}
            except Exception as e:  # noqa: BLE001
                result = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    except Exception as e:  # noqa: BLE001
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve_stdio(stdin=None, stdout=None) -> int:
    from ..store.db import connect
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    conn = connect()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}) + "\n")
            stdout.flush()
            continue
        resp = handle(conn, msg)
        if resp is not None:
            stdout.write(json.dumps(resp, default=str) + "\n")
            stdout.flush()
    return 0
