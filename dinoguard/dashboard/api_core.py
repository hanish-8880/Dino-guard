"""Core read APIs: home, sessions, trace, stream, metrics, queries, policies."""
from __future__ import annotations

import os

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import config, paths
from ..engine.modes import effective
from ..engine.registry import SELF_PROTECT, build
from ..store.db import rows
from ..store.queries import QueryError, safe_select
from ..util import new_id, now_iso
from . import metrics as m
from .app import get_conn, require_token
from .trace import build_trace

router = APIRouter(prefix="/api")


@router.get("/home")
def home(conn=Depends(get_conn)):
    return m.home(conn)


@router.get("/sessions")
def sessions(harness: str | None = None, project: str | None = None, status: str | None = None,
             since: str | None = None, session: str | None = None, model: str | None = None,
             environment: str | None = None, event: str | None = None, has_deny: bool = False,
             min_cost: float | None = None, min_tokens: int | None = None, eval_name: str | None = None,
             min_score: float | None = None, max_score: float | None = None, limit: int = 100,
             conn=Depends(get_conn)):
    where, args = [], []
    for col, val, op in (("s.harness", harness, "="), ("s.cwd", project and f"%{project}%", " LIKE "),
                         ("s.status", status, "="), ("s.last_ts", since, " >= "), ("s.id", session and f"{session}%",
                         " LIKE "), ("s.model", model and f"%{model}%", " LIKE "), ("s.environment", environment, "=")):
        if val:
            where.append(f"{col}{op}?")
            args.append(val)
    if event:
        where.append("EXISTS (SELECT 1 FROM events e WHERE e.session_id=s.id AND e.event=?)")
        args.append(event)
    if has_deny:
        where.append("EXISTS (SELECT 1 FROM events e WHERE e.session_id=s.id AND e.decision='deny')")
    if eval_name and (min_score is not None or max_score is not None):
        where.append("EXISTS (SELECT 1 FROM eval_results r JOIN evals v ON v.id=r.eval_id WHERE r.session_id=s.id "
                     "AND v.name=? AND r.score >= ? AND r.score <= ?)")
        args += [eval_name, -1e9 if min_score is None else min_score, 1e9 if max_score is None else max_score]
    having = []
    if min_cost is not None:
        having.append(f"cost >= {float(min_cost)}")
    if min_tokens is not None:
        having.append(f"tokens >= {int(min_tokens)}")
    sql = ("SELECT * FROM (SELECT s.*, (SELECT COUNT(*) FROM events e WHERE e.session_id=s.id AND "
           "e.event='PreToolUse') tools,"
           " (SELECT COUNT(*) FROM events e WHERE e.session_id=s.id AND e.decision='deny') denies,"
           " (SELECT COALESCE(SUM(cost_usd),0) FROM model_calls mc WHERE mc.session_id=s.id) cost,"
           " (SELECT COALESCE(SUM(tokens_in+tokens_out+cache_read+cache_write),0) FROM model_calls mc"
           " WHERE mc.session_id=s.id) tokens"
           " FROM sessions s" + (" WHERE " + " AND ".join(where) if where else "") + ")" +
           (" WHERE " + " AND ".join(having) if having else "") + " ORDER BY last_ts DESC LIMIT ?")
    return rows(conn, sql, (*args, min(limit, 500)))


@router.get("/sessions/{sid}/export")
def export(sid: str, format: str = "json", conn=Depends(get_conn)):
    from fastapi.responses import PlainTextResponse
    from ..analytics.export import export_session, to_jsonl
    try:
        doc = export_session(conn, sid)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    if format == "jsonl":
        return PlainTextResponse(to_jsonl(doc), media_type="application/x-ndjson", headers={
            "Content-Disposition": f'attachment; filename="dinoguard-{sid[:12]}.jsonl"'})
    return doc


@router.get("/compare")
def compare(a: str | None = None, b: str | None = None, by: str | None = None, days: int = 30,
            conn=Depends(get_conn)):
    from ..analytics import compare as cmp
    try:
        if by:
            return {"by": by, "groups": cmp.compare_groups(conn, by, days=days)}
        if not (a and b):
            raise HTTPException(400, "pass a and b session ids, or by=model|harness|environment|outcome")
        return cmp.compare_two(conn, a, b)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e)) from e


@router.get("/errors")
def errors(days: int = 7, limit: int = 50, conn=Depends(get_conn)):
    from ..analytics.errors import groups
    return groups(conn, days=days, limit=min(limit, 200))


@router.get("/analytics/tools")
def analytics_tools(days: int = 7, conn=Depends(get_conn)):
    from ..analytics.stats import tools
    return tools(conn, days=days)


@router.get("/analytics/policies")
def analytics_policies(days: int = 7, conn=Depends(get_conn)):
    from ..analytics.stats import policies
    return policies(conn, days=days)


@router.get("/sessions/{sid}/trace")
def trace(sid: str, conn=Depends(get_conn)):
    t = build_trace(conn, sid)
    if not t["session"]:
        raise HTTPException(404, "no such session")
    return t


@router.get("/stream")
def stream(after: int = -1, limit: int = 200):
    """Return new stream lines after byte offset `after` (-1 = last 50 lines)."""
    import json
    p = paths.stream_path()
    if not p.exists():
        return {"offset": 0, "records": []}
    size = p.stat().st_size
    if after < 0 or after > size:
        after = max(0, size - 32 * 1024)
        tail = True
    else:
        tail = False
    with open(p, "rb") as f:
        f.seek(after)
        data = f.read(4 * 1024 * 1024)
    end = data.rfind(b"\n")
    if end < 0:
        return {"offset": after, "records": []}
    chunk = data[: end + 1]
    lines = chunk.decode("utf-8", "replace").splitlines()
    if tail and after > 0:
        lines = lines[1:]
    recs = []
    for ln in lines[-limit:] if tail else lines:
        try:
            recs.append(json.loads(ln))
        except ValueError:
            pass
    return {"offset": after + len(chunk), "records": recs}


@router.get("/metrics")
def metrics(hours: int = 168, conn=Depends(get_conn)):
    return m.metrics(conn, hours)


@router.post("/query")
def query(body: dict = Body(...), conn=Depends(get_conn)):
    try:
        cols, out = safe_select(conn, str(body.get("sql", "")), timeout_s=5.0)
    except QueryError as e:
        raise HTTPException(400, str(e)) from e
    return {"columns": cols, "rows": out}


@router.get("/queries")
def saved_queries(conn=Depends(get_conn)):
    return rows(conn, "SELECT * FROM saved_queries ORDER BY created_at DESC")


@router.post("/queries", dependencies=[Depends(require_token)])
def save_query(body: dict = Body(...), conn=Depends(get_conn)):
    from ..store.queries import check_select
    try:
        check_select(str(body.get("sql", "")))
    except QueryError as e:
        raise HTTPException(400, str(e)) from e
    qid = new_id("q")
    conn.execute("INSERT INTO saved_queries(id, name, sql, created_at) VALUES (?,?,?,?)",
                 (qid, body.get("name") or "tile", body["sql"], now_iso()))
    conn.commit()
    return {"id": qid}


@router.delete("/queries/{qid}", dependencies=[Depends(require_token)])
def delete_query(qid: str, conn=Depends(get_conn)):
    conn.execute("DELETE FROM saved_queries WHERE id=?", (qid,))
    conn.commit()
    return {"ok": True}


@router.get("/policies")
def policies(conn=Depends(get_conn)):
    cfg = config.load()
    reg = build(os.getcwd())
    hits = {(r["policy"], r["decision"], r["mode"]): r["n"] for r in rows(
        conn, "SELECT policy, decision, mode, COUNT(*) n FROM decisions GROUP BY policy, decision, mode")}
    out = []
    for p in reg.policies:
        e = effective(p, cfg)
        act = {k[1] + ("" if k[2] == "enforce" else "_observed"): v for k, v in hits.items() if k[0] == p.name}
        out.append({**p.summary(), "enabled": e.enabled, "mode": e.mode, "params": e.params, "activity": act})
    return {"policies": out, "errors": reg.errors}


@router.get("/policies/{name}/activity")
def policy_activity(name: str, limit: int = 100, conn=Depends(get_conn)):
    return rows(conn, "SELECT d.*, e.ts, e.session_id, e.tool_name, e.input_json FROM decisions d JOIN events e "
                      "ON e.id=d.event_id WHERE d.policy=? ORDER BY e.ts DESC LIMIT ?", (name, limit))


@router.post("/policies/{name}", dependencies=[Depends(require_token)])
def set_policy(name: str, body: dict = Body(...)):
    if name == SELF_PROTECT:
        raise HTTPException(400, "guard-self-protect is always on and enforced")
    fields = {k: body[k] for k in ("enabled", "mode") if k in body}
    if "mode" in fields and fields["mode"] not in ("observe", "enforce"):
        raise HTTPException(400, "mode must be observe|enforce")
    if "params" in body and isinstance(body["params"], dict):
        fields["params"] = body["params"]
    config.set_policy(name, **fields)
    try:
        from ..daemon.client import request
        request("reload", timeout=2.0)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}
