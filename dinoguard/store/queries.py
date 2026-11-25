"""Read-side queries: session history for policies, safe SQL for dashboards/assistant."""
from __future__ import annotations

import re
import sqlite3
import time

from ..engine.context import History
from .db import rows
from .writer import load_json


def session_history(conn: sqlite3.Connection, session_id: str, limit: int = 200) -> History:
    evs = rows(conn, "SELECT id, ts, event, tool_name, input_json, error, output_excerpt, decision FROM events "
                     "WHERE session_id=? ORDER BY ts DESC, rowid DESC LIMIT ?", (session_id, limit))
    evs.reverse()
    ids = [e["id"] for e in evs]
    decs: dict[str, list] = {}
    if ids:
        q = ",".join("?" * len(ids))
        for d in rows(conn, f"SELECT event_id, policy, decision, mode FROM decisions WHERE event_id IN ({q})", ids):
            decs.setdefault(d["event_id"], []).append(d)
    items = []
    for e in evs:
        items.append({"id": e["id"], "ts": e["ts"], "event": e["event"], "tool_name": e["tool_name"],
                      "input": load_json(e["input_json"], {}), "error": e["error"], "output": e["output_excerpt"],
                      "decision": e["decision"], "decisions": decs.get(e["id"], [])})
    srow = conn.execute("SELECT first_prompt FROM sessions WHERE id=?", (session_id,)).fetchone()
    first = srow["first_prompt"] if srow else None
    prompts = [i["input"].get("prompt") for i in items if i["event"] == "UserPromptSubmit" and i["input"].get("prompt")]
    if first and (not prompts or prompts[0] != first):
        prompts.insert(0, first)
    return History(items, first_prompt=first or (prompts[0] if prompts else None), prompts=prompts)


_FORBIDDEN = re.compile(r"(?i)\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex|"
                        r"analyze|begin|commit|rollback|savepoint|release|load_extension)\b")


class QueryError(ValueError):
    pass


def check_select(sql: str) -> str:
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise QueryError("only a single statement is allowed")
    if not re.match(r"(?is)^\s*(select|with)\b", s):
        raise QueryError("only SELECT queries are allowed")
    if _FORBIDDEN.search(re.sub(r"'[^']*'", "''", s)):
        raise QueryError("query contains a forbidden keyword")
    return s


def safe_select(conn: sqlite3.Connection, sql: str, params=(), timeout_s: float = 5.0, max_rows: int = 1000):
    """Run a read-only SELECT with a wall-clock timeout. Returns (columns, rows)."""
    s = check_select(sql)
    deadline = time.monotonic() + timeout_s
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cur = conn.execute(s, params)
        cols = [c[0] for c in cur.description or []]
        out = [list(r) for r in cur.fetchmany(max_rows)]
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e):
            raise QueryError(f"query exceeded {timeout_s:.0f}s timeout") from e
        raise QueryError(str(e)) from e
    finally:
        conn.set_progress_handler(None, 0)
    return cols, out
