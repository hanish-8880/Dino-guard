"""Persist events + decisions (redacted) and append to the live stream file."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any

from .. import paths
from ..secrets.scanner import redact, redact_obj
from ..util import dumps, new_id, now_iso, truncate

STREAM_MAX_BYTES = 10 * 1024 * 1024
_stream_lock = threading.Lock()


def summarize(event) -> str:
    inp = event.tool_input
    if event.tool_name == "Bash":
        s = str(inp.get("command", ""))
    elif event.tool_name in ("Read", "Write", "Edit"):
        s = str(inp.get("file_path", ""))
    elif event.tool_name in ("Grep", "Glob"):
        s = str(inp.get("pattern", ""))
    elif event.tool_name in ("WebFetch", "WebSearch"):
        s = str(inp.get("url") or inp.get("query") or "")
    elif event.prompt:
        s = event.prompt
    else:
        s = ""
    return (redact(" ".join(s.split())) or "")[:160]


def append_stream(record: dict[str, Any]) -> None:
    p = paths.stream_path()
    line = dumps(record) + "\n"
    with _stream_lock:
        try:
            if p.exists() and p.stat().st_size > STREAM_MAX_BYTES:
                rotated = p.with_suffix(".jsonl.1")
                if rotated.exists():
                    rotated.unlink()
                os.replace(p, rotated)
        except OSError:
            pass
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)


def _upsert_session(conn: sqlite3.Connection, ev, tokens=None) -> None:
    s = ev.session
    sid = ev.session_id
    row = conn.execute("SELECT id, first_prompt FROM sessions WHERE id=?", (sid,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO sessions(id, harness, cwd, started_at, first_prompt, status, transcript_path, last_ts)"
                     " VALUES (?,?,?,?,?,?,?,?)",
                     (sid, ev.harness, s.get("cwd"), ev.ts, redact(ev.prompt) if ev.event == "UserPromptSubmit" else None,
                      "active", s.get("transcript_path"), ev.ts))
    else:
        if ev.event == "UserPromptSubmit" and not row["first_prompt"]:
            conn.execute("UPDATE sessions SET first_prompt=? WHERE id=?", (redact(ev.prompt), sid))
        conn.execute("UPDATE sessions SET last_ts=?, transcript_path=COALESCE(?, transcript_path) WHERE id=?",
                     (ev.ts, s.get("transcript_path"), sid))
    if ev.event == "SessionEnd":
        conn.execute("UPDATE sessions SET ended_at=?, status='ended' WHERE id=?", (ev.ts, sid))
    if tokens is not None and tokens.turns:
        conn.execute("UPDATE sessions SET model=?, cost_usd=?, tokens_in=?, tokens_out=?, tokens_cache=? WHERE id=?",
                     (tokens.model, tokens.cost_usd, tokens.tokens_in, tokens.tokens_out,
                      tokens.cache_read + tokens.cache_write, sid))


def record(conn: sqlite3.Connection, evaluation, tokens=None, latency_ms: float | None = None) -> None:
    ev = evaluation.event
    d = evaluation.decision
    inp: dict[str, Any] = dict(ev.tool_input) if ev.tool else {}
    if ev.prompt is not None and not inp:
        inp = {"prompt": ev.prompt}
    if ev.extra:
        inp = {**inp, "_extra": ev.extra} if not ev.tool else inp
    parent_id = None
    if ev.tool_use_id and ev.event != "PreToolUse":
        r = conn.execute("SELECT id FROM events WHERE tool_use_id=? AND event='PreToolUse' LIMIT 1",
                         (ev.tool_use_id,)).fetchone()
        parent_id = r["id"] if r else None
    _upsert_session(conn, ev, tokens)
    conn.execute(
        "INSERT OR REPLACE INTO events(id, session_id, ts, event, tool_name, tool_class, input_json, output_excerpt,"
        " duration_ms, error, parent_id, agent_id, tool_use_id, harness, decision, latency_ms)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ev.id, ev.session_id, ev.ts, ev.event, ev.tool_name, (ev.tool or {}).get("class"),
         dumps(redact_obj(inp)), redact(truncate(ev.output, 2000)), ev.duration_ms, redact(truncate(ev.error, 1000)),
         parent_id, ev.agent_id, ev.tool_use_id, ev.harness, d.kind, latency_ms))
    for r in evaluation.results:
        if r.decision.kind == "allow" and not r.error and not r.reviewer_verdict:
            continue   # keep the table small: only non-allow results, errors and reviews
        conn.execute(
            "INSERT INTO decisions(id, event_id, policy, version, mode, decision, reason, authority, reviewer_verdict,"
            " latency_ms, enforced, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("dec"), ev.id, r.policy, r.version, r.mode, r.decision.kind, redact(r.decision.reason),
             r.authority, dumps(r.reviewer_verdict) if r.reviewer_verdict else None, r.latency_ms,
             1 if r.enforced else 0, r.error))
    conn.commit()
    append_stream(stream_record(evaluation, tokens))


def stream_record(evaluation, tokens=None, kind: str = "event") -> dict[str, Any]:
    ev, d = evaluation.event, evaluation.decision
    return {
        "kind": kind, "ts": ev.ts, "id": ev.id, "session": ev.session_id, "harness": ev.harness,
        "event": ev.event, "tool": ev.tool_name, "summary": summarize(ev), "decision": d.kind,
        "policy": d.policy, "reason": redact(d.reason),
        "observed": [{"policy": r.policy, "decision": r.decision.kind} for r in evaluation.observed],
        "tokens": tokens.to_dict() if tokens is not None else None,
    }


def stream_note(kind: str, **fields) -> None:
    append_stream({"kind": kind, "ts": now_iso(), **fields})


def load_json(text: str | None, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default
