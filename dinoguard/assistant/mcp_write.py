"""MCP write tools (CLAUDE.md §17.11). Each needs a human yes before it applies.

Flow: the agent calls a write tool -> DinoGuard records an approval request (and sends it to the approvals
channel if one is set up) and answers "pending, code NNNN". The human approves with
`dinoguard approve answer NNNN yes`, the dashboard, or the phone. The agent calls the tool again with
`approval_code` and the SAME arguments; only then is the change applied. The agent itself can't approve: the
guard blocks `dinoguard approve` and dashboard writes from agents.
"""
from __future__ import annotations

import json
import secrets as _secrets
import sqlite3

from .. import config
from ..util import new_id, now_iso

WRITE_TOOLS = [
    {"name": "ack_insight", "description": "Acknowledge an insight (needs human approval).",
     "inputSchema": {"type": "object", "properties": {"insight_id": {"type": "string"},
                                                      "approval_code": {"type": "string"}}, "required": ["insight_id"]}},
    {"name": "resolve_insight", "description": "Mark an insight resolved (needs human approval).",
     "inputSchema": {"type": "object", "properties": {"insight_id": {"type": "string"}, "comment": {"type": "string"},
                                                      "approval_code": {"type": "string"}}, "required": ["insight_id"]}},
    {"name": "set_policy_mode", "description": "Set a policy to observe, enforce or disabled (needs human approval).",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"},
                                                      "mode": {"type": "string", "enum": ["observe", "enforce", "disabled"]},
                                                      "approval_code": {"type": "string"}}, "required": ["name", "mode"]}},
    {"name": "run_audit", "description": "Run a local audit (needs human approval).",
     "inputSchema": {"type": "object", "properties": {"days": {"type": "integer", "default": 30},
                                                      "goal": {"type": "string"}, "project": {"type": "string"},
                                                      "approval_code": {"type": "string"}}}},
]
NAMES = {t["name"] for t in WRITE_TOOLS}


def _summary(name: str, args: dict) -> str:
    clean = {k: v for k, v in sorted(args.items()) if k != "approval_code"}
    return f"mcp:{name} {json.dumps(clean, sort_keys=True, default=str)}"[:300]


def _validate(conn: sqlite3.Connection, name: str, args: dict) -> None:
    if name in ("ack_insight", "resolve_insight"):
        if not conn.execute("SELECT 1 FROM insights WHERE id=?", (str(args.get("insight_id")),)).fetchone():
            raise KeyError(f"no insight {args.get('insight_id')!r}")
    if name == "set_policy_mode":
        from ..engine.registry import SELF_PROTECT, build
        if args.get("mode") not in ("observe", "enforce", "disabled"):
            raise ValueError("mode must be observe, enforce or disabled")
        if args.get("name") == SELF_PROTECT:
            raise PermissionError("guard-self-protect is always on and always enforced")
        if build(None).get(str(args.get("name"))) is None:
            raise KeyError(f"unknown policy {args.get('name')!r}")


def _request(conn: sqlite3.Connection, name: str, args: dict, sender=None) -> dict:
    code = f"{_secrets.randbelow(10000):04d}"
    summary = _summary(name, args)
    cfg = config.load()
    channel = (cfg.get("approvals") or {}).get("channel")
    conn.execute("INSERT INTO approvals(id, event_id, channel, sent_at, code, summary) VALUES (?,?,?,?,?,?)",
                 (new_id("apr"), None, channel or "local", now_iso(), code, summary))
    conn.commit()
    if channel:
        text = f"DinoGuard: an agent asks to {summary[4:]}\nReply YES {code} or NO {code}."
        try:
            (sender or _send)(channel, text, cfg)
        except Exception:  # noqa: BLE001 - the CLI / dashboard can still answer
            pass
    return {"status": "pending_approval", "approval_code": code,
            "message": f"Ask the user to approve with `dinoguard approve answer {code} yes` (or on the dashboard / "
                       "phone), then call this tool again with the same arguments and approval_code."}


def _send(channel, text, cfg):
    from ..alerts.channels import send
    send(channel, text, cfg)


def _approved(conn: sqlite3.Connection, name: str, args: dict) -> str | None:
    """None = approved and consumed; otherwise the reason it isn't."""
    r = conn.execute("SELECT id, answer, summary FROM approvals WHERE code=? AND event_id IS NULL AND summary LIKE "
                     "'mcp:%' ORDER BY sent_at DESC LIMIT 1", (str(args.get("approval_code")),)).fetchone()
    if not r:
        return "no approval request with that code"
    if r["summary"] != _summary(name, args):
        return "that approval was for a different action; request a new one"
    if r["answer"] is None:
        return "the human has not answered yet"
    if r["answer"] != "yes":
        return "the human declined (or it was already used)"
    conn.execute("UPDATE approvals SET answer='used' WHERE id=?", (r["id"],))      # one approval = one change
    conn.commit()
    return None


def _apply(conn: sqlite3.Connection, name: str, args: dict):
    if name in ("ack_insight", "resolve_insight"):
        iid = str(args["insight_id"])
        status = "ack" if name == "ack_insight" else "resolved"
        conn.execute("UPDATE insights SET status=?, resolved_at=CASE WHEN ?='resolved' THEN ? ELSE resolved_at END "
                     "WHERE id=?", (status, status, now_iso(), iid))
        if args.get("comment"):
            r = conn.execute("SELECT comments_json FROM insights WHERE id=?", (iid,)).fetchone()
            cs = json.loads(r["comments_json"] or "[]")
            cs.append({"ts": now_iso(), "text": f"(via MCP, human-approved) {str(args['comment'])[:2000]}"})
            conn.execute("UPDATE insights SET comments_json=? WHERE id=?", (json.dumps(cs), iid))
        conn.commit()
        return {"insight_id": iid, "status": status}
    if name == "set_policy_mode":
        if args["mode"] == "disabled":
            config.set_policy(args["name"], enabled=False)
        else:
            config.set_policy(args["name"], enabled=True, mode=args["mode"])
        from ..cli.pol import _reload_daemon
        _reload_daemon()
        return {"policy": args["name"], "mode": args["mode"]}
    if name == "run_audit":
        from ..audit.replay import run_audit
        res = run_audit(conn, days=int(args.get("days") or 30), goal=args.get("goal"), project=args.get("project"))
        return {k: res[k] for k in ("sessions", "tool_calls", "hits", "coverage")} | {
            "findings": [{k: f.get(k) for k in ("id", "policy", "failure_mode", "severity", "count")}
                         for f in res["findings"][:30]]}
    raise KeyError(name)


def call_write(conn: sqlite3.Connection, name: str, args: dict, sender=None):
    _validate(conn, name, args)
    if not args.get("approval_code"):
        return _request(conn, name, args, sender)
    why = _approved(conn, name, args)
    if why:
        raise PermissionError(why)
    return {"status": "applied", "result": _apply(conn, name, args)}
