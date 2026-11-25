"""Build the session dict evals see, and run one eval (code / judge / classifier) against it."""
from __future__ import annotations

import json
import sqlite3

from ..store.db import one, rows
from ..store.writer import load_json
from ..util import new_id, now_iso, parse_iso
from .safe_eval import safe_eval


def session_dict(conn: sqlite3.Connection, sid: str) -> dict | None:
    s = one(conn, "SELECT * FROM sessions WHERE id=?", (sid,))
    if not s:
        return None
    evs = rows(conn, "SELECT event, tool_name, decision, error, input_json, ts FROM events WHERE session_id=? "
                     "ORDER BY ts", (sid,))
    prompts = [load_json(e["input_json"], {}).get("prompt") for e in evs if e["event"] == "UserPromptSubmit"]
    prompts = [p for p in prompts if p]
    tools: dict[str, int] = {}
    for e in evs:
        if e["event"] == "PreToolUse":
            tools[e["tool_name"]] = tools.get(e["tool_name"], 0) + 1
    tok = one(conn, "SELECT COALESCE(SUM(tokens_in+tokens_out+cache_read+cache_write),0) t, COALESCE(SUM(cost_usd),0) c "
                    "FROM model_calls WHERE session_id=?", (sid,))
    dur = 0.0
    if s.get("started_at") and (s.get("ended_at") or s.get("last_ts")):
        dur = (parse_iso(s.get("ended_at") or s["last_ts"]) - parse_iso(s["started_at"])).total_seconds()
    return {
        "id": sid, "harness": s["harness"], "cwd": s["cwd"], "status": s["status"], "model": s["model"],
        "first_prompt": s["first_prompt"] or (prompts[0] if prompts else ""), "prompts": prompts,
        "cost_usd": tok["c"] or s["cost_usd"] or 0, "tokens": tok["t"], "duration_s": dur,
        "tool_calls": sum(tools.values()), "tools": tools,
        "denies": sum(1 for e in evs if e["event"] == "PreToolUse" and e["decision"] == "deny"),
        "instructs": sum(1 for e in evs if e["decision"] == "instruct"),
        "errors": sum(1 for e in evs if e["error"]),
        "events": [{"event": e["event"], "tool": e["tool_name"], "decision": e["decision"], "error": bool(e["error"])}
                   for e in evs],
    }


def _normalize(value) -> dict:
    if isinstance(value, bool):
        return {"score": 1.0 if value else 0.0, "passed": value}
    if isinstance(value, (int, float)):
        v = float(value)
        return {"score": v, "passed": v >= 0.5} if 0 <= v <= 1 else {"metric": v, "score": None, "passed": None}
    if isinstance(value, dict):
        out = {k: value.get(k) for k in ("score", "passed", "metric", "unit", "reasoning")}
        if out["passed"] is None and isinstance(out["score"], (int, float)):
            out["passed"] = out["score"] >= 0.5
        return out
    raise ValueError(f"eval returned {type(value).__name__}; expected bool, number or dict")


def run_eval(ev: dict, sess: dict) -> dict:
    kind = ev["kind"]
    if kind == "code":
        return _normalize(safe_eval(ev["spec"], sess))
    if kind in ("judge", "classifier"):
        from ..llm import complete_json
        brief = {k: sess[k] for k in ("first_prompt", "prompts", "tool_calls", "denies", "errors", "tools", "cost_usd")}
        system = ("You grade AI coding-agent sessions. Return JSON {\"score\": 0..1" +
                  (", \"reasoning\": \"<two sentences>\"}" if kind == "judge" else "}") + ".")
        raw = complete_json(system, f"Rubric: {ev['spec']}\nSession: {json.dumps(brief)[:6000]}")
        out = _normalize({"score": float(raw.get("score", 0)), "reasoning": raw.get("reasoning")})
        if kind == "classifier":
            out["reasoning"] = None
        return out
    raise ValueError(f"{kind} evals are run by an external worker")


def condition_ok(ev: dict, sess: dict) -> bool:
    cond = (ev.get("condition") or "").strip()
    return True if not cond else bool(safe_eval(cond, sess))


def store_result(conn, ev: dict, sid: str, res: dict) -> None:
    conn.execute("INSERT INTO eval_results(id, eval_id, version, session_id, score, passed, metric, unit, reasoning, "
                 "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (new_id("er"), ev["id"], ev["version"], sid, res.get("score"),
                  None if res.get("passed") is None else int(bool(res["passed"])), res.get("metric"), res.get("unit"),
                  res.get("reasoning"), now_iso()))
    conn.commit()


def run_for_session(conn, sid: str, cfg: dict | None = None) -> int:
    """Score a finished session with every deployed (enabled) eval whose condition matches."""
    from .manage import ensure_builtin
    ensure_builtin(conn)
    sess = session_dict(conn, sid)
    if not sess:
        return 0
    n = 0
    for ev in rows(conn, "SELECT * FROM evals WHERE enabled=1 AND kind != 'worker'"):
        if conn.execute("SELECT 1 FROM eval_results WHERE eval_id=? AND session_id=?", (ev["id"], sid)).fetchone():
            continue
        try:
            if not condition_ok(ev, sess):
                continue
            store_result(conn, ev, sid, run_eval(ev, sess))
            n += 1
        except Exception:  # noqa: BLE001 - a broken eval must not break the hook
            continue
    return n
