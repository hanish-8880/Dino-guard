"""Retention: delete stored data older than N days (CLAUDE.md §17.1). The daemon runs it once a day."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from .. import paths
from ..util import now_iso, parse_iso

DAY_S = 86400


def cutoff_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _count(conn, sql, args) -> int:
    return conn.execute(sql, args).fetchone()[0]


def prune(conn: sqlite3.Connection, days: int, dry_run: bool = False) -> dict:
    """Delete events, decisions, model calls and finished sessions older than `days`. Returns counts."""
    if days <= 0:
        raise ValueError("days must be >= 1")
    cut = cutoff_iso(days)
    old_sessions = ("SELECT id FROM sessions WHERE COALESCE(ended_at, last_ts, started_at) < ? "
                    "AND (status='ended' OR COALESCE(last_ts, started_at) < ?)")
    counts = {
        "events": _count(conn, "SELECT COUNT(*) FROM events WHERE ts < ?", (cut,)),
        "decisions": _count(conn, "SELECT COUNT(*) FROM decisions WHERE event_id IN (SELECT id FROM events WHERE ts < ?)",
                            (cut,)),
        "model_calls": _count(conn, "SELECT COUNT(*) FROM model_calls WHERE ts < ?", (cut,)),
        "sessions": _count(conn, f"SELECT COUNT(*) FROM ({old_sessions})", (cut, cut)),
        "approvals": _count(conn, "SELECT COUNT(*) FROM approvals WHERE sent_at < ?", (cut,)),
        "alert_firings": _count(conn, "SELECT COUNT(*) FROM alert_firings WHERE ts < ?", (cut,)),
        "reviewer_cache": _count(conn, "SELECT COUNT(*) FROM reviewer_cache WHERE created_at < ?", (cut,)),
    }
    counts["stream_lines"] = _trim_stream(cut, dry_run)
    if dry_run:
        return {**counts, "cutoff": cut, "dry_run": True}
    conn.execute("DELETE FROM decisions WHERE event_id IN (SELECT id FROM events WHERE ts < ?)", (cut,))
    conn.execute("DELETE FROM events WHERE ts < ?", (cut,))
    conn.execute("DELETE FROM model_calls WHERE ts < ?", (cut,))
    conn.execute(f"DELETE FROM eval_results WHERE session_id IN ({old_sessions})", (cut, cut))
    conn.execute(f"DELETE FROM transcript_offsets WHERE session_id IN ({old_sessions})", (cut, cut))
    conn.execute(f"DELETE FROM sessions WHERE id IN ({old_sessions})", (cut, cut))
    conn.execute("DELETE FROM approvals WHERE sent_at < ?", (cut,))
    conn.execute("DELETE FROM alert_firings WHERE ts < ?", (cut,))
    conn.execute("DELETE FROM reviewer_cache WHERE created_at < ?", (cut,))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('last_prune', ?)", (now_iso(),))
    conn.commit()
    return {**counts, "cutoff": cut, "dry_run": False}


def _trim_stream(cut: str, dry_run: bool) -> int:
    """Drop stream.jsonl lines older than the cutoff (the runner treats a shrunk file as a rotation)."""
    dropped = 0
    rotated = paths.stream_path().with_suffix(".jsonl.1")
    cut_ts = parse_iso(cut).timestamp()
    if rotated.exists() and rotated.stat().st_mtime < cut_ts:
        dropped += sum(1 for _ in open(rotated, "rb"))
        if not dry_run:
            rotated.unlink()
    p = paths.stream_path()
    if not p.exists():
        return dropped
    keep: list[str] = []
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                ts = json.loads(line).get("ts") or ""
            except ValueError:
                ts = ""
            if ts and ts < cut:
                dropped += 1
            else:
                keep.append(line)
    if dropped and not dry_run:
        tmp = p.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(keep)
        os.replace(tmp, p)
    return dropped


def run_due(conn: sqlite3.Connection, cfg: dict) -> dict | None:
    """Daemon tick: prune once a day when retention_days > 0."""
    days = int(cfg.get("retention_days") or 0)
    if days <= 0:
        return None
    r = conn.execute("SELECT value FROM meta WHERE key='last_prune'").fetchone()
    if r and r[0]:
        try:
            if (datetime.now(timezone.utc) - parse_iso(r[0])).total_seconds() < DAY_S:
                return None
        except ValueError:
            pass
    return prune(conn, days)
