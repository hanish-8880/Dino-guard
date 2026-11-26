"""Hook drift: remember native event / tool names an adapter did not recognise (CLAUDE.md §17.7).

Unknown events are still evaluated and stored as usual; this table only tells `dinoguard doctor` that a harness
may have changed its hook format since the adapter was verified.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from ..engine.events import EVENTS
from ..util import now_iso
from .db import rows

_KNOWN = set(EVENTS)


def _known_tools(harness: str) -> frozenset[str] | None:
    try:
        from ..adapters import get_adapter
        return getattr(get_adapter(harness), "known_native_tools", None)
    except Exception:  # noqa: BLE001
        return None


def note(conn: sqlite3.Connection, harness: str, version: str | None, kind: str, value: str) -> None:
    ts = now_iso()
    conn.execute("INSERT INTO hook_drift(harness, harness_version, kind, value, first_seen, last_seen, count) "
                 "VALUES (?,?,?,?,?,?,1) ON CONFLICT(harness, kind, value) DO UPDATE SET last_seen=excluded.last_seen,"
                 " count=count+1, harness_version=COALESCE(excluded.harness_version, harness_version)",
                 (harness, version, kind, str(value)[:120], ts, ts))
    conn.commit()


def observe(conn: sqlite3.Connection, event) -> None:
    """Called once per handled event. Never raises."""
    try:
        if event.event not in _KNOWN:
            note(conn, event.harness, event.harness_version, "event", event.event)
        tool = event.tool or {}
        native = tool.get("native_name")
        if tool.get("name") == "Other" and native:
            known = _known_tools(event.harness)
            if known is not None and native not in known:
                note(conn, event.harness, event.harness_version, "tool", native)
    except Exception:  # noqa: BLE001 - drift tracking must never break a hook
        pass


def recent(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return rows(conn, "SELECT * FROM hook_drift WHERE last_seen >= ? ORDER BY harness, kind, count DESC", (since,))
