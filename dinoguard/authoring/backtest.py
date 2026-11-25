"""Backtest a policy (draft, custom or deployed) against stored past events."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from ..engine.context import History, PolicyContext
from ..engine.decisions import allow
from ..engine.events import CanonicalEvent, make_tool
from ..store.db import connect, rows
from ..store.writer import load_json
from ..audit.replay import NullProbe


def _resolve(name: str):
    try:
        from .draft import load_draft
        return load_draft(name)[0]
    except (FileNotFoundError, ValueError):
        from ..engine.registry import build
        p = build(os.getcwd()).get(name)
        if p is None:
            raise KeyError(f"no draft or loaded policy named {name!r}") from None
        return p


def backtest(name: str, days: int = 30, conn=None, policy=None) -> dict:
    pol = policy or _resolve(name)
    conn = conn or connect()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    evs = rows(conn, "SELECT e.*, s.cwd FROM events e LEFT JOIN sessions s ON s.id=e.session_id WHERE e.ts >= ? "
                     "ORDER BY e.session_id, e.ts, e.rowid", (since,))
    post_ok = {r["parent_id"] for r in rows(conn, "SELECT parent_id FROM events WHERE ts >= ? AND parent_id IS NOT NULL "
                                                  "AND event='PostToolUse' AND error IS NULL", (since,))}
    hist: dict[str, list] = {}
    prompts: dict[str, list] = {}
    blocked = interrupted = instructed = evaluated = 0
    hits = []
    for e in evs:
        sid = e["session_id"]
        items = hist.setdefault(sid, [])
        inp = load_json(e["input_json"], {}) or {}
        ps = prompts.setdefault(sid, [])
        if e["event"] == "UserPromptSubmit" and inp.get("prompt"):
            ps.append(inp["prompt"])
        tool = make_tool(e["tool_name"], inp) if e["tool_name"] else None
        ev = CanonicalEvent(event=e["event"], harness=e["harness"] or "?", session={"id": sid, "cwd": e["cwd"]},
                            tool=tool, prompt=inp.get("prompt"), output=e["output_excerpt"], error=e["error"], ts=e["ts"],
                            id=e["id"])
        if pol.matches(ev.event, ev.tool_name):
            evaluated += 1
            ctx = PolicyContext(event=ev, params=dict(pol.params), probe=NullProbe(), policy_name=pol.name,
                                history=History(items, first_prompt=ps[0] if ps else None, prompts=ps))
            try:
                d = pol.fn(ctx) or allow()
            except Exception as ex:  # noqa: BLE001
                d = allow(f"error: {ex}")
            if d.kind != "allow":
                working = e["id"] in post_ok
                if d.kind == "deny":
                    blocked += 1
                    interrupted += 1 if working else 0
                else:
                    instructed += 1
                hits.append({"ts": e["ts"], "session": sid, "decision": d.kind, "tool": e["tool_name"],
                             "summary": str(inp.get("command") or inp.get("file_path") or inp.get("prompt") or "")[:120],
                             "working_call": working, "reason": d.reason})
        items.append({"event": e["event"], "tool_name": e["tool_name"], "input": inp, "error": e["error"],
                      "decisions": []})
    return {"policy": pol.name, "days": days, "events": len(evs), "evaluated": evaluated, "blocked": blocked,
            "interrupted": interrupted, "instructed": instructed, "hits": hits}
