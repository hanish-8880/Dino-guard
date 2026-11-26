"""Trace export (already-redacted store data; never raw native payloads)."""
from __future__ import annotations

import json
import sqlite3

from ..dashboard.trace import build_trace
from ..version import __version__
from .compare import _resolve


def export_session(conn: sqlite3.Connection, sid: str) -> dict:
    sid = _resolve(conn, sid)
    t = build_trace(conn, sid)
    return {"format": "dinoguard-trace", "version": 1, "dinoguard": __version__, **t}


def to_jsonl(doc: dict) -> str:
    """One line per item: a session header, then every hook event, model call and decision in order."""
    lines = [json.dumps({"type": "session", **(doc.get("session") or {})}, default=str)]

    def walk(items):
        for it in items:
            children = it.get("children") or []
            lines.append(json.dumps({k: v for k, v in it.items() if k != "children"}, default=str))
            walk(children)
    walk(doc.get("items") or [])
    return "\n".join(lines) + "\n"
