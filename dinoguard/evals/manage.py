"""Eval lifecycle: new (draft, v0) -> test (not stored) -> deploy (immutable version) -> backfill / results."""
from __future__ import annotations

import sqlite3

from ..store.db import one, rows
from ..util import new_id, now_iso
from .runner import condition_ok, run_eval, session_dict, store_result
from .safe_eval import validate

SENTIMENT_SPEC = '{"score": 1 - frustration(prompts), "passed": frustration(prompts) < 0.5}'


def ensure_builtin(conn: sqlite3.Connection) -> None:
    if not conn.execute("SELECT 1 FROM evals WHERE name='sentiment'").fetchone():
        conn.execute("INSERT INTO evals(id, name, kind, spec, condition, version, enabled, created_at) VALUES "
                     "(?,?,?,?,?,?,?,?)", (new_id("ev"), "sentiment", "code", SENTIMENT_SPEC, "len(prompts) > 0", 1, 1,
                                           now_iso()))
        conn.commit()


def new(conn, name: str, kind: str, spec: str, condition: str | None = None) -> dict:
    if not name:
        raise ValueError("eval name required")
    if kind == "code":
        validate(spec)
    if condition:
        validate(condition)
    conn.execute("DELETE FROM evals WHERE name=? AND version=0", (name,))
    conn.execute("INSERT INTO evals(id, name, kind, spec, condition, version, enabled, created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (new_id("ev"), name, kind, spec, condition, 0, 0, now_iso()))
    conn.commit()
    return {"name": name, "kind": kind, "spec": spec, "condition": condition}


def _draft(conn, name: str) -> dict:
    d = one(conn, "SELECT * FROM evals WHERE name=? ORDER BY version=0 DESC, version DESC LIMIT 1", (name,))
    if not d:
        raise KeyError(f"no eval named {name!r}")
    return d


def _ended_sessions(conn, limit: int) -> list[str]:
    return [r["id"] for r in rows(conn, "SELECT id FROM sessions ORDER BY COALESCE(ended_at, last_ts) DESC LIMIT ?", (limit,))]


def test(conn, name: str, limit: int = 20) -> list[dict]:
    ev = _draft(conn, name)
    out = []
    for sid in _ended_sessions(conn, limit):
        sess = session_dict(conn, sid)
        try:
            if not condition_ok(ev, sess):
                continue
            res = run_eval(ev, sess)
        except Exception as e:  # noqa: BLE001
            res = {"score": None, "passed": None, "reasoning": f"error: {e}"}
        out.append({"session_id": sid, **res})
    return out


def deploy(conn, name: str) -> int:
    d = _draft(conn, name)
    v = (one(conn, "SELECT MAX(version) v FROM evals WHERE name=?", (name,))["v"] or 0) + 1
    conn.execute("UPDATE evals SET enabled=0 WHERE name=?", (name,))
    conn.execute("INSERT INTO evals(id, name, kind, spec, condition, version, enabled, created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (new_id("ev"), name, d["kind"], d["spec"], d["condition"], v, 1, now_iso()))
    conn.commit()
    return v


def _current(conn, name: str) -> dict:
    ev = one(conn, "SELECT * FROM evals WHERE name=? AND enabled=1", (name,))
    if not ev:
        raise KeyError(f"{name} is not deployed (dinoguard eval deploy {name})")
    return ev


def backfill(conn, name: str, limit: int = 200) -> int:
    ev = _current(conn, name)
    n = 0
    for sid in _ended_sessions(conn, limit):
        if conn.execute("SELECT 1 FROM eval_results WHERE eval_id=? AND session_id=?", (ev["id"], sid)).fetchone():
            continue
        sess = session_dict(conn, sid)
        try:
            if ev["kind"] == "worker" or not condition_ok(ev, sess):
                continue
            store_result(conn, ev, sid, run_eval(ev, sess))
            n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


def results(conn, name: str, limit: int = 50) -> list[dict]:
    return rows(conn, "SELECT r.*, e.name FROM eval_results r JOIN evals e ON e.id=r.eval_id WHERE e.name=? "
                      "ORDER BY r.created_at DESC LIMIT ?", (name, limit))


def list_evals(conn) -> list[dict]:
    ensure_builtin(conn)
    return rows(conn, "SELECT name, kind, spec, condition, MAX(version) version, MAX(enabled) enabled FROM evals "
                      "GROUP BY name ORDER BY name")


def worker_pending(conn, name: str, limit: int = 20) -> list[dict]:
    ev = _current(conn, name)
    out = []
    for sid in _ended_sessions(conn, limit * 3):
        if conn.execute("SELECT 1 FROM eval_results WHERE eval_id=? AND session_id=?", (ev["id"], sid)).fetchone():
            continue
        out.append(session_dict(conn, sid))
        if len(out) >= limit:
            break
    return out


def worker_store(conn, name: str, items: list[dict]) -> int:
    ev = _current(conn, name)
    n = 0
    for it in items:
        if not it.get("session_id"):
            continue
        store_result(conn, ev, it["session_id"], {k: it.get(k) for k in ("score", "passed", "metric", "unit", "reasoning")})
        n += 1
    return n


def rollback(conn, name: str) -> int:
    """Redeploy the version before the current one, as a new immutable version (CLAUDE.md §17.5)."""
    cur = _current(conn, name)
    prev = one(conn, "SELECT * FROM evals WHERE name=? AND version>0 AND version<? ORDER BY version DESC LIMIT 1",
               (name, cur["version"]))
    if not prev:
        raise ValueError(f"{name} has no earlier version to roll back to")
    v = (one(conn, "SELECT MAX(version) v FROM evals WHERE name=?", (name,))["v"] or 0) + 1
    conn.execute("UPDATE evals SET enabled=0 WHERE name=?", (name,))
    conn.execute("INSERT INTO evals(id, name, kind, spec, condition, version, enabled, created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (new_id("ev"), name, prev["kind"], prev["spec"], prev["condition"], v, 1, now_iso()))
    conn.commit()
    return prev["version"]


def compare(conn, name: str, by: str) -> list[dict]:
    """Average score + pass rate of an eval per harness / environment / model."""
    if by not in ("harness", "environment", "model"):
        raise ValueError("--by must be harness, environment or model")
    out = rows(conn, f"SELECT COALESCE(s.{by}, 'unknown') grp, COUNT(*) n, AVG(r.score) avg_score, "
                     "AVG(r.passed) pass_rate FROM eval_results r JOIN evals e ON e.id=r.eval_id "
                     f"LEFT JOIN sessions s ON s.id=r.session_id WHERE e.name=? GROUP BY grp ORDER BY n DESC", (name,))
    for r in out:
        r["avg_score"] = None if r["avg_score"] is None else round(r["avg_score"], 4)
        r["pass_rate"] = None if r["pass_rate"] is None else round(r["pass_rate"], 4)
    return out
