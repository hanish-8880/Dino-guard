"""Audit run history, coverage and the recurrence check (CLAUDE.md §17.4)."""
from __future__ import annotations

import json
import sqlite3

from ..store.db import rows
from ..util import new_id, now_iso


def coverage(conn: sqlite3.Connection, scanned: set[str], since: str, by_project: dict[str, set]) -> dict:
    """How much of what the store knows about was replayed by this audit."""
    known = {r["id"] for r in rows(conn, "SELECT id FROM sessions WHERE COALESCE(last_ts, started_at) >= ?", (since,))}
    covered = known & scanned
    return {"sessions_scanned": len(scanned), "store_sessions": len(known), "store_sessions_covered": len(covered),
            "ratio": round(len(covered) / len(known), 4) if known else 1.0,
            "by_project": {p: len(s) for p, s in sorted(by_project.items(), key=lambda kv: -len(kv[1]))}}


def record_run(conn: sqlite3.Connection, audit_id: str, started: str, res: dict, cov: dict, goal: str | None,
               scope: dict) -> str:
    rid = new_id("run")
    conn.execute("INSERT INTO audit_runs(id, audit_id, started_at, finished_at, sessions, tool_calls, findings, "
                 "coverage_json, goal, scope_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (rid, audit_id, started, now_iso(), res["sessions"], res["tool_calls"], len(res["findings"]),
                  json.dumps(cov), goal, json.dumps(scope)))
    return rid


def history(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    out = rows(conn, "SELECT * FROM audit_runs ORDER BY started_at DESC LIMIT ?", (limit,))
    for r in out:
        r["coverage"] = json.loads(r.pop("coverage_json") or "{}")
        r["scope"] = json.loads(r.pop("scope_json") or "{}")
    return out


def check_recurrence(conn: sqlite3.Connection, findings: list[dict], cfg: dict | None = None,
                     notify: bool = True) -> list[dict]:
    """Reopen resolved insights whose failure shows up again after they were resolved, and alert."""
    reopened = []
    for f in findings:
        fp = f.get("fingerprint") or f["policy"]
        for ins in rows(conn, "SELECT * FROM insights WHERE status='resolved' AND fingerprint=?", (fp,)):
            resolved_at = ins.get("resolved_at") or ""
            if not resolved_at or (f.get("last_ts") or "") <= resolved_at:
                continue
            comments = json.loads(ins.get("comments_json") or "[]")
            comments.append({"ts": now_iso(), "text": f"recurred: {f['count']} new hit(s) after it was resolved "
                                                      f"({len(f['session_ids'])} session(s))"})
            conn.execute("UPDATE insights SET status='open', resolved_at=NULL, comments_json=?, finding_id=? "
                         "WHERE id=?", (json.dumps(comments), f.get("id"), ins["id"]))
            reopened.append({"insight": ins["id"], "fingerprint": fp, "title": ins["title"], "severity": f["severity"]})
    conn.commit()
    if reopened and notify:
        _alert(reopened, cfg)
    return reopened


def _alert(reopened: list[dict], cfg: dict | None) -> None:
    from .. import config
    from ..alerts.engine import route_channels
    from ..alerts.channels import send
    cfg = cfg or config.load()
    for r in reopened:
        sev = "critical" if r["severity"] == "high" else "warning"
        for ch in route_channels([], sev, cfg):
            try:
                send(ch, f"[{sev.upper()}] DinoGuard: a fixed failure came back: {r['title']}", cfg,
                     subject="DinoGuard: failure recurred")
            except Exception:  # noqa: BLE001 - alerts are best effort
                pass
