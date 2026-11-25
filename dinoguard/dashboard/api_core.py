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
             since: str | None = None, limit: int = 100, conn=Depends(get_conn)):
    where, args = [], []
    if harness:
        where.append("s.harness=?")
        args.append(harness)
    if project:
        where.append("s.cwd LIKE ?")
        args.append(f"%{project}%")
    if status:
        where.append("s.status=?")
        args.append(status)
    if since:
        where.append("s.last_ts >= ?")
        args.append(since)
    sql = ("SELECT s.*, (SELECT COUNT(*) FROM events e WHERE e.session_id=s.id AND e.event='PreToolUse') tools,"
           " (SELECT COUNT(*) FROM events e WHERE e.session_id=s.id AND e.decision='deny') denies,"
           " (SELECT COALESCE(SUM(cost_usd),0) FROM model_calls mc WHERE mc.session_id=s.id) cost"
           " FROM sessions s" + (" WHERE " + " AND ".join(where) if where else "") +
           " ORDER BY s.last_ts DESC LIMIT ?")
    return rows(conn, sql, (*args, min(limit, 500)))


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
