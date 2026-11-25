"""Audit schedule (run by the daemon tick) + optional email report."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from ..store.db import rows
from ..util import new_id, now_iso, parse_iso


def _next(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def set_schedule(conn: sqlite3.Connection, days: int, email: str | None = None) -> str:
    if not 1 <= days <= 90:
        raise ValueError("schedule must be 1-90 days")
    r = conn.execute("SELECT id FROM audits WHERE cadence_days IS NOT NULL LIMIT 1").fetchone()
    if r:
        conn.execute("UPDATE audits SET cadence_days=?, next_run=?, email=? WHERE id=?", (days, _next(days), email, r["id"]))
        aid = r["id"]
    else:
        aid = new_id("aud")
        conn.execute("INSERT INTO audits(id, goal, scope_json, cadence_days, next_run, email) VALUES (?,?,?,?,?,?)",
                     (aid, "scheduled local audit", json.dumps({}), days, _next(days), email))
    conn.commit()
    return aid


def unschedule(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE audits SET cadence_days=NULL, next_run=NULL WHERE cadence_days IS NOT NULL")
    conn.commit()


def list_audits(conn: sqlite3.Connection) -> list[dict]:
    return rows(conn, "SELECT id, goal, cadence_days, last_run, next_run, email FROM audits ORDER BY last_run DESC")


def run_due(conn: sqlite3.Connection, cfg: dict) -> int:
    from .replay import run_audit
    due = [a for a in rows(conn, "SELECT * FROM audits WHERE cadence_days IS NOT NULL AND next_run IS NOT NULL")
           if parse_iso(a["next_run"]) <= datetime.now(timezone.utc)]
    for a in due:
        res = run_audit(conn, days=max(7, a["cadence_days"]))
        conn.execute("UPDATE audits SET next_run=? WHERE id=?", (_next(a["cadence_days"]), a["id"]))
        conn.commit()
        if a.get("email"):
            try:
                email_report(res, a["email"], cfg)
            except Exception:  # noqa: BLE001
                pass
    return len(due)


def report_text(res: dict) -> str:
    lines = [f"DinoGuard audit {now_iso()[:10]}: {res['sessions']} sessions, {res['tool_calls']} tool calls.",
             "Findings are evidence for review, not proof.", ""]
    for f in res["findings"][:20]:
        lines.append(f"[{f['severity']}] {f['failure_mode']} x{f['count']} in {len(f['session_ids'])} sessions")
    return "\n".join(lines)


def email_report(res: dict, to: str, cfg: dict | None = None) -> None:
    from .. import config
    from ..alerts.channels import send_email
    cfg = cfg or config.load()
    ch = next((c for c in (cfg.get("channels") or {}).values() if c.get("type") == "email"), None)
    if not ch:
        raise RuntimeError("no email channel configured: dinoguard channel mail email smtp_host=... from=...")
    send_email({**ch, "to": to}, report_text(res), subject="DinoGuard audit report")
