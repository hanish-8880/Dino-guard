"""Alert rules: evaluate metric / error / eval-score / eval-combo / SQL triggers; firing opens an insight."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from ..dashboard.metrics import pct
from ..store.db import one, rows
from ..store.queries import QueryError, safe_select
from ..util import new_id, now_iso, parse_iso
from . import nl

OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
       "==": lambda a, b: a == b}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def metric_value(conn: sqlite3.Connection, metric: str, start: str, end: str) -> float:
    q = {
        "denies": "SELECT COUNT(*) FROM events WHERE decision='deny' AND ts>=? AND ts<=?",
        "instructs": "SELECT COUNT(*) FROM events WHERE decision='instruct' AND ts>=? AND ts<=?",
        "errors": "SELECT COUNT(*) FROM events WHERE error IS NOT NULL AND ts>=? AND ts<=?",
        "events": "SELECT COUNT(*) FROM events WHERE ts>=? AND ts<=?",
        "sessions": "SELECT COUNT(DISTINCT session_id) FROM events WHERE ts>=? AND ts<=?",
        "cost_usd": "SELECT COALESCE(SUM(cost_usd),0) FROM model_calls WHERE ts>=? AND ts<=?",
        "tokens": "SELECT COALESCE(SUM(tokens_in+tokens_out+cache_read+cache_write),0) FROM model_calls WHERE ts>=? AND ts<=?",
        "approvals_timeout": "SELECT COUNT(*) FROM approvals WHERE answer IS NULL AND sent_at>=? AND sent_at<=?",
    }.get(metric)
    if metric == "hook_latency_p95_ms":
        vals = [r[0] for r in conn.execute("SELECT latency_ms FROM events WHERE ts>=? AND ts<=?", (start, end))]
        return float(pct(vals, .95) or 0)
    if not q:
        raise ValueError(f"unknown metric {metric!r}")
    return float(conn.execute(q, (start, end)).fetchone()[0] or 0)


def _eval_score(conn, name: str, start: str, end: str) -> float | None:
    r = conn.execute("SELECT AVG(r.score) FROM eval_results r JOIN evals e ON e.id=r.eval_id WHERE e.name=? "
                     "AND r.created_at>=? AND r.created_at<=?", (name, start, end)).fetchone()
    return r[0]


def evaluate_spec(conn: sqlite3.Connection, spec: dict, at: datetime | None = None) -> tuple[bool, float | None]:
    at = at or datetime.now(timezone.utc)
    end = _iso(at)
    start = _iso(at - timedelta(seconds=int(spec.get("window_s") or 3600)))
    kind = spec.get("trigger_kind")
    if kind in ("metric", "errors"):
        v = metric_value(conn, spec.get("metric") or "errors", start, end)
    elif kind == "eval_score":
        v = _eval_score(conn, spec["eval"], start, end)
        if v is None:
            return False, None
    elif kind == "eval_combo":
        results = [evaluate_spec(conn, {**s, "trigger_kind": "eval_score"}, at) for s in spec.get("all", [])]
        return bool(results) and all(r[0] for r in results), sum(1 for r in results if r[0])
    elif kind == "sql":
        try:
            cols, out = safe_select(conn, spec["sql"], timeout_s=5.0, max_rows=1)
        except QueryError:
            return False, None
        v = float(out[0][0]) if out and out[0] and isinstance(out[0][0], (int, float)) else 0.0
    else:
        raise ValueError(f"unknown trigger_kind {kind!r}")
    return OPS[spec.get("op", ">")](v, float(spec.get("value", 0))), v


def create(conn, name: str, spec: dict, channels: list[str] | None = None, severity: str = "warning",
           description: str = "", check_interval_s: int = 60, consecutive_checks: int = 1) -> dict:
    aid = new_id("al")
    conn.execute("INSERT INTO alerts(id, name, description, enabled, trigger_kind, trigger_spec_json, severity, "
                 "channels_json, check_interval_s, consecutive_checks) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (aid, name, description, 1, spec["trigger_kind"], json.dumps(spec), severity,
                  json.dumps(channels or []), check_interval_s, consecutive_checks))
    conn.commit()
    return {"id": aid, "name": name, "trigger_kind": spec["trigger_kind"], "trigger_spec": spec}


def create_from_text(conn, text: str, channels=None, severity: str = "warning") -> dict:
    spec = nl.parse(text)
    name = text.strip()[:80] or spec["trigger_kind"]
    return create(conn, name, spec, channels, severity, description=text)


def _load(row: dict) -> dict:
    row = dict(row)
    row["trigger_spec"] = json.loads(row.pop("trigger_spec_json") or "{}")
    row["channels"] = json.loads(row.pop("channels_json") or "[]")
    return row


def list_alerts(conn) -> list[dict]:
    return [_load(r) for r in rows(conn, "SELECT * FROM alerts ORDER BY name")]


def delete(conn, aid: str) -> None:
    conn.execute("DELETE FROM alerts WHERE id=?", (aid,))
    conn.commit()


def preview_spec(conn, spec: dict, hours: int = 24, step_s: int = 300, consecutive: int = 1) -> dict:
    """How many times would this have fired in the last N hours (rising edges after N consecutive hits)."""
    now = datetime.now(timezone.utc)
    if spec.get("trigger_kind") == "sql":
        ok, v = evaluate_spec(conn, spec, now)
        return {"would_fire": int(ok), "points": 1, "value_now": v}
    fired, streak, in_breach, last_v = 0, 0, False, None
    t = now - timedelta(hours=hours)
    points = 0
    while t <= now:
        ok, last_v = evaluate_spec(conn, spec, t)
        streak = streak + 1 if ok else 0
        if streak >= consecutive and not in_breach:
            fired += 1
            in_breach = True
        if not ok:
            in_breach = False
        t += timedelta(seconds=step_s)
        points += 1
    return {"would_fire": fired, "points": points, "value_now": last_v}


def preview(conn, aid: str) -> dict:
    a = one(conn, "SELECT * FROM alerts WHERE id=?", (aid,))
    if not a:
        raise KeyError(aid)
    a = _load(a)
    return preview_spec(conn, a["trigger_spec"], consecutive=a["consecutive_checks"] or 1)


def route_channels(own: list[str] | None, severity: str, cfg: dict) -> list[str]:
    """An alert's own channels win; otherwise use config.alerts.routes[severity] (CLAUDE.md §17.8)."""
    if own:
        return list(own)
    routes = ((cfg.get("alerts") or {}).get("routes") or {})
    return list(routes.get(severity) or [])


def _notify(a: dict, text: str, cfg: dict) -> tuple[list[str], dict]:
    from .channels import send
    sent, errors = [], {}
    for ch in route_channels(a.get("channels"), a.get("severity") or "warning", cfg):
        try:
            send(ch, text, cfg, subject=f"DinoGuard alert: {a['name']}")
            sent.append(ch)
        except Exception as e:  # noqa: BLE001
            errors[ch] = str(e)
    return sent, errors


def fire(conn, a: dict, value, cfg: dict) -> dict:
    iid = new_id("ins")
    title = f"Alert '{a['name']}' fired (value {value})"
    conn.execute("INSERT INTO insights(id, source, alert_id, status, title, created_at) VALUES (?,?,?,?,?,?)",
                 (iid, "alert", a["id"], "open", title, now_iso()))
    conn.execute("INSERT INTO alert_firings(id, alert_id, ts, value, insight_id) VALUES (?,?,?,?,?)",
                 (new_id("af"), a["id"], now_iso(), value, iid))
    conn.commit()
    sent, errors = _notify(a, f"[{a['severity'].upper()}] {title}", cfg)
    return {"alert": a["id"], "insight": iid, "value": value, "sent": sent, "errors": errors}


def check_all(conn, cfg: dict, force: bool = False) -> list[dict]:
    fired = []
    now = datetime.now(timezone.utc)
    for a in list_alerts(conn):
        if not a["enabled"]:
            continue
        if not force and a.get("last_checked"):
            if (now - parse_iso(a["last_checked"])).total_seconds() < (a["check_interval_s"] or 60):
                continue
        ok, v = evaluate_spec(conn, a["trigger_spec"], now)
        hits = (a.get("hits") or 0) + 1 if ok else 0
        conn.execute("UPDATE alerts SET hits=?, last_checked=? WHERE id=?", (hits, now_iso(), a["id"]))
        conn.commit()
        if ok and hits == (a["consecutive_checks"] or 1):   # edge-triggered: fire once per breach
            fired.append(fire(conn, a, v, cfg))
    return fired


def check_due(conn, cfg: dict) -> list[dict]:
    return check_all(conn, cfg, force=False)


def test_notification(conn, aid: str | None) -> dict:
    from .. import config
    cfg = config.load()
    if aid:
        a = one(conn, "SELECT * FROM alerts WHERE id=?", (aid,))
        if not a:
            return {"sent": [], "errors": {"alert": "not found"}}
        a = _load(a)
    else:
        a = {"name": "test", "channels": list((cfg.get("channels") or {}).keys()), "severity": "warning"}
    sent, errors = _notify(a, f"DinoGuard test notification for '{a['name']}'", cfg)
    return {"sent": sent, "errors": errors}
