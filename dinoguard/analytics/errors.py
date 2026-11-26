"""Error grouping: normalise tool error text into a fingerprint and group by it."""
from __future__ import annotations

import hashlib
import re
import sqlite3

from ..store.db import rows
from .stats import since_iso

_RULES = [
    (re.compile(r"(['\"`])(?:(?!\1).){1,200}\1"), "<str>"),
    (re.compile(r"(?:\b[a-z]:)?(?:[\\/][\w.\-@~]+(?: [\w.\-@~]+)*){2,}[\\/]?", re.I), "<path>"),
    (re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{8,}\b", re.I), "<hex>"),
    (re.compile(r"\d+(?:\.\d+)?"), "<n>"),
    (re.compile(r"\s+"), " "),
]


def normalise(text: str | None) -> str:
    s = (text or "").strip().lower()
    for rx, rep in _RULES:
        s = rx.sub(rep, s)
    return s.strip()[:160]


def fingerprint(text: str | None) -> str:
    return hashlib.sha1(normalise(text).encode("utf-8")).hexdigest()[:12]


def groups(conn: sqlite3.Connection, days: int = 7, limit: int = 50) -> list[dict]:
    out: dict[str, dict] = {}
    for r in rows(conn, "SELECT id, session_id, ts, tool_name, error FROM events WHERE error IS NOT NULL AND "
                        "error != '' AND ts >= ? ORDER BY ts", (since_iso(days),)):
        key = normalise(r["error"])
        g = out.get(key)
        if g is None:
            g = out[key] = {"fingerprint": fingerprint(r["error"]), "pattern": key, "count": 0, "tools": {},
                            "sessions": set(), "first_seen": r["ts"], "last_seen": r["ts"],
                            "example": (r["error"] or "")[:300], "example_event": r["id"]}
        g["count"] += 1
        g["tools"][r["tool_name"] or "?"] = g["tools"].get(r["tool_name"] or "?", 0) + 1
        g["sessions"].add(r["session_id"])
        g["last_seen"] = r["ts"]
    res = sorted(out.values(), key=lambda g: (-g["count"], g["pattern"]))[:limit]
    for g in res:
        g["sessions"] = sorted(g["sessions"])
    return res
