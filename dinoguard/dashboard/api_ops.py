"""Operational APIs: approvals, alerts, insights/findings, audit, evals, settings, assistant, OTel ingest."""
from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .. import config
from ..store.db import rows
from ..store.writer import load_json
from ..util import now_iso
from .app import get_conn, require_token

router = APIRouter(prefix="/api")
W = [Depends(require_token)]


# ---- approvals ------------------------------------------------------------
@router.get("/approvals")
def approvals(conn=Depends(get_conn)):
    return rows(conn, "SELECT * FROM approvals ORDER BY sent_at DESC LIMIT 100")


@router.post("/approvals/{code}", dependencies=W)
def answer_approval(code: str, body: dict = Body(...), conn=Depends(get_conn)):
    from ..approvals.flow import answer
    return {"ok": answer(conn, code, bool(body.get("yes")))}


@router.post("/approvals/twilio/inbound", response_class=PlainTextResponse)
async def twilio_inbound(request: Request, conn=Depends(get_conn)):
    """Twilio WhatsApp webhook (form-encoded). Only replies from the configured approver number are accepted."""
    from urllib.parse import parse_qs

    from ..approvals.flow import answer, parse_reply
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode("utf-8", "replace")).items()}
    cfg = config.load()
    ch = (cfg.get("channels") or {}).get((cfg.get("approvals") or {}).get("channel") or "", {})
    if not ch.get("to") or ch["to"] not in form.get("From", ""):
        raise HTTPException(403, "unknown sender")
    parsed = parse_reply(form.get("Body", ""))
    if parsed and parsed[1]:
        answer(conn, parsed[1], parsed[0])
    return "<Response></Response>"


# ---- alerts / insights / findings -----------------------------------------
@router.get("/alerts")
def alerts(conn=Depends(get_conn)):
    from ..alerts import engine
    return engine.list_alerts(conn)


@router.post("/alerts", dependencies=W)
def new_alert(body: dict = Body(...), conn=Depends(get_conn)):
    from ..alerts import engine
    a = engine.create_from_text(conn, body.get("text", ""), channels=body.get("channels") or [],
                                severity=body.get("severity", "warning"))
    return {**a, "preview": engine.preview(conn, a["id"])}


@router.post("/alerts/preview")
def preview_alert(body: dict = Body(...), conn=Depends(get_conn)):
    from ..alerts import engine, nl
    spec = nl.parse(body.get("text", ""))
    return {"spec": spec, **engine.preview_spec(conn, spec)}


@router.post("/alerts/{aid}/test", dependencies=W)
def test_alert(aid: str, conn=Depends(get_conn)):
    from ..alerts import engine
    return engine.test_notification(conn, aid)


@router.delete("/alerts/{aid}", dependencies=W)
def delete_alert(aid: str, conn=Depends(get_conn)):
    from ..alerts import engine
    engine.delete(conn, aid)
    return {"ok": True}


@router.get("/insights")
def insights(status: str | None = None, conn=Depends(get_conn)):
    q = "SELECT * FROM insights" + (" WHERE status=?" if status else "") + " ORDER BY created_at DESC LIMIT 200"
    out = rows(conn, q, (status,) if status else ())
    for r in out:
        r["comments"] = load_json(r.pop("comments_json"), [])
    return out


@router.post("/insights/{iid}", dependencies=W)
def update_insight(iid: str, body: dict = Body(...), conn=Depends(get_conn)):
    r = conn.execute("SELECT comments_json FROM insights WHERE id=?", (iid,)).fetchone()
    if not r:
        raise HTTPException(404)
    if body.get("status") in ("open", "ack", "assigned", "resolved"):
        conn.execute("UPDATE insights SET status=?, resolved_at=CASE WHEN ?='resolved' THEN ? ELSE NULL END "
                     "WHERE id=?", (body["status"], body["status"], now_iso(), iid))
    if "owner" in body:
        conn.execute("UPDATE insights SET owner=?, status=CASE WHEN status='open' THEN 'assigned' ELSE status END "
                     "WHERE id=?", (body["owner"], iid))
    if body.get("comment"):
        cs = load_json(r["comments_json"], [])
        cs.append({"ts": now_iso(), "text": str(body["comment"])[:2000]})
        conn.execute("UPDATE insights SET comments_json=? WHERE id=?", (json.dumps(cs), iid))
    conn.commit()
    return {"ok": True}


@router.get("/findings")
def findings(conn=Depends(get_conn)):
    out = rows(conn, "SELECT * FROM findings ORDER BY created_at DESC LIMIT 200")
    for r in out:
        r["evidence"] = load_json(r.pop("evidence_json"), [])
        r["session_ids"] = load_json(r["session_ids"], [])
    return out


@router.post("/audit/run", dependencies=W)
def run_audit(body: dict = Body(default={}), conn=Depends(get_conn)):
    from ..audit.replay import run_audit as _run
    return _run(conn, days=int(body.get("days", 30)), goal=body.get("goal"), project=body.get("project"),
                sessions=body.get("sessions") or None, env=body.get("env"), context=body.get("context"))


@router.get("/audit/overview")
def audit_overview(conn=Depends(get_conn)):
    from ..audit.replay import overview
    return overview(conn)


@router.post("/policies-draft", dependencies=W)
def draft_policy(body: dict = Body(...)):
    from ..authoring.draft import draft_policy as _d
    if not str(body.get("text", "")).strip():
        raise HTTPException(400, "describe the rule in plain English")
    return _d(str(body["text"]))


@router.post("/findings/{fid}/draft", dependencies=W)
def draft_from_finding(fid: str, body: dict = Body(default={}), conn=Depends(get_conn)):
    """Draft a policy from a finding; with {"install": true} also publish + deploy it in observe mode."""
    from ..authoring.draft import draft_from_finding as _d
    try:
        d = _d(conn, fid)
    except KeyError as e:
        raise HTTPException(404, f"no finding {e}") from e
    if body.get("install"):
        from ..deploy import versions
        v = versions.publish(d["name"], notes=f"suggested from finding {fid}", conn=conn)
        versions.deploy(d["name"], v, "observe", conn)
        d.update({"installed": True, "version": v, "mode": "observe"})
    return d


@router.get("/audit/runs")
def audit_runs(conn=Depends(get_conn)):
    from ..audit.runs import history
    return history(conn)


# ---- evals ---------------------------------------------------------------
@router.get("/evals")
def evals(conn=Depends(get_conn)):
    from ..evals import manage
    return manage.list_evals(conn)


@router.get("/evals/{name}/results")
def eval_results(name: str, conn=Depends(get_conn)):
    from ..evals import manage
    return manage.results(conn, name, limit=500)


@router.get("/evals/worker/pending")
def worker_pending(eval: str, limit: int = 20, conn=Depends(get_conn)):  # noqa: A002
    from ..evals import manage
    return manage.worker_pending(conn, eval, limit)


@router.post("/evals/worker/results", dependencies=W)
def worker_results(body: dict = Body(...), conn=Depends(get_conn)):
    from ..evals import manage
    return {"stored": manage.worker_store(conn, body.get("eval"), body.get("results") or [])}


# ---- settings / assistant / otel ------------------------------------------
_SETTINGS_KEYS = ("reviewer", "approvals", "channels", "runner", "llm")


@router.get("/settings")
def get_settings():
    cfg = config.load()
    from ..tokens.prices import user_prices_path
    return {**{k: cfg.get(k) for k in _SETTINGS_KEYS}, "prices_file": str(user_prices_path()),
            "budget_usd": (cfg.get("policies", {}).get("token-budget", {}).get("params") or {}).get("budget_usd")}


@router.post("/settings", dependencies=W)
def put_settings(body: dict = Body(...)):
    def apply(cfg):
        for k in _SETTINGS_KEYS:
            if isinstance(body.get(k), dict):
                cfg[k] = {**(cfg.get(k) or {}), **body[k]}
        if "budget_usd" in body:
            cfg.setdefault("policies", {}).setdefault("token-budget", {}).setdefault("params", {})["budget_usd"] = \
                body["budget_usd"]
    config.update(apply)
    return {"ok": True}


@router.post("/ask")
def ask(body: dict = Body(...), conn=Depends(get_conn)):
    from ..assistant.nl2sql import ask as _ask
    from ..assistant.chats import record
    res = _ask(conn, str(body.get("question", "")))
    res["chat_id"] = record(conn, body.get("chat_id"), str(body.get("question", "")), res)
    return res


@router.post("/v1/events", dependencies=W)
def sdk_events(body: dict | list = Body(...)):
    """Agents in any language send SDK wire-format events (one, a list, or {"events": [...]}); each is evaluated
    like a hook event and the decisions come back in order (CLAUDE.md §17.9)."""
    from ..adapters.sdk import SDKAdapter, evaluate_event
    items = body.get("events") if isinstance(body, dict) and "events" in body else body
    items = items if isinstance(items, list) else [items]
    if len(items) > 500:
        raise HTTPException(413, "at most 500 events per request")
    out = []
    for raw in items:
        if not isinstance(raw, dict):
            raise HTTPException(400, "each event must be a JSON object")
        d = evaluate_event(SDKAdapter().to_canonical(raw))
        out.append({"decision": d.kind, "reason": d.reason, "policy": d.policy})
    return {"decisions": out}


@router.post("/v1/traces")
async def otel_traces(request: Request, conn=Depends(get_conn)):
    from ..otel.ingest import ingest_otlp_json
    try:
        payload = await request.json()
    except ValueError as e:
        raise HTTPException(400, "expected OTLP/JSON") from e
    return {"ingested": ingest_otlp_json(conn, payload)}
