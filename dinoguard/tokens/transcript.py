"""Incremental reader for harness transcripts (Claude Code JSONL) -> model_calls rows + TokenStats."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import timedelta

from ..engine.context import TokenStats
from ..util import new_id, now_iso, parse_iso
from . import prices


def parse_usage_line(line: str) -> dict | None:
    """Return {ts, model, in, out, cache_read, cache_write, msg_id} for an assistant message line."""
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return None
    msg = obj.get("message") or {}
    usage = msg.get("usage") or {}
    if not usage:
        return None
    return {
        "ts": obj.get("timestamp") or now_iso(), "model": msg.get("model"),
        "in": int(usage.get("input_tokens") or 0), "out": int(usage.get("output_tokens") or 0),
        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "cache_write": int(usage.get("cache_creation_input_tokens") or 0),
        "msg_id": msg.get("id") or obj.get("uuid"),
    }


def ingest(conn: sqlite3.Connection, session_id: str, path: str | None) -> int:
    """Read new bytes since the stored offset; insert one model_calls row per assistant message id."""
    if not path or not os.path.isfile(path):
        return 0
    row = conn.execute("SELECT path, offset FROM transcript_offsets WHERE session_id=?", (session_id,)).fetchone()
    offset = row["offset"] if row and row["path"] == path else 0
    size = os.path.getsize(path)
    if size < offset:
        offset = 0   # file rotated/truncated
    if size == offset:
        return 0
    added = 0
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    last_nl = data.rfind(b"\n")
    if last_nl < 0:
        return 0
    chunk, new_offset = data[: last_nl + 1], offset + last_nl + 1
    for raw in chunk.decode("utf-8", errors="replace").splitlines():
        u = parse_usage_line(raw)
        if not u:
            continue
        rid = f"mc_{session_id}_{u['msg_id']}" if u["msg_id"] else new_id("mc")
        c = prices.cost(u["model"], u["in"], u["out"], u["cache_read"], u["cache_write"])
        cur = conn.execute(
            "INSERT OR REPLACE INTO model_calls(id, session_id, ts, model, tokens_in, tokens_out, cache_read, cache_write,"
            " cost_usd, latency_ms, context_tokens) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rid, session_id, u["ts"], u["model"], u["in"], u["out"], u["cache_read"], u["cache_write"], c, None,
             u["in"] + u["cache_read"] + u["cache_write"]))
        added += cur.rowcount
    conn.execute("INSERT OR REPLACE INTO transcript_offsets(session_id, path, offset) VALUES (?,?,?)",
                 (session_id, path, new_offset))
    conn.commit()
    return added


def stats(conn: sqlite3.Connection, session_id: str, now: str | None = None) -> TokenStats:
    agg = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(tokens_in),0) i, COALESCE(SUM(tokens_out),0) o,"
                       " COALESCE(SUM(cache_read),0) cr, COALESCE(SUM(cache_write),0) cw, COALESCE(SUM(cost_usd),0) c"
                       " FROM model_calls WHERE session_id=?", (session_id,)).fetchone()
    last = conn.execute("SELECT model, context_tokens, ts FROM model_calls WHERE session_id=? ORDER BY ts DESC LIMIT 1",
                        (session_id,)).fetchone()
    st = TokenStats(tokens_in=agg["i"], tokens_out=agg["o"], cache_read=agg["cr"], cache_write=agg["cw"],
                    cost_usd=agg["c"], turns=agg["n"])
    if last:
        st.model = last["model"]
        st.context_tokens = last["context_tokens"] or 0
        st.context_window = int(prices.for_model(last["model"]).get("context_window", 200000))
        ref = parse_iso(now) if now else parse_iso(last["ts"])
        since = (ref - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%S")
        burn = conn.execute("SELECT COALESCE(SUM(tokens_in + tokens_out + cache_write),0) b FROM model_calls "
                            "WHERE session_id=? AND ts >= ?", (session_id, since)).fetchone()
        st.burn_per_min = float(burn["b"])
    return st


def session_tokens(conn: sqlite3.Connection, session_id: str, transcript_path: str | None) -> TokenStats:
    try:
        ingest(conn, session_id, transcript_path)
    except (OSError, sqlite3.Error):
        pass
    return stats(conn, session_id, now_iso())
