"""'Ask your data': question -> read-only SQL over the local store -> rows + cited session ids."""
from __future__ import annotations

import re
import sqlite3

from ..store.queries import QueryError, check_select, safe_select

SCHEMA = """sessions(id, harness, cwd, started_at, ended_at, last_ts, first_prompt, model, status, cost_usd, tokens_in, tokens_out, tokens_cache)
events(id, session_id, ts, event, tool_name, tool_class, input_json, output_excerpt, duration_ms, error, parent_id, agent_id, harness, decision, latency_ms)
decisions(id, event_id, policy, version, mode, decision, reason, authority, reviewer_verdict, latency_ms, enforced)
model_calls(id, session_id, ts, model, tokens_in, tokens_out, cache_read, cache_write, cost_usd, latency_ms, context_tokens)
findings(id, audit_id, failure_mode, severity, evidence_json, session_ids, count, created_at)
insights(id, source, finding_id, alert_id, status, owner, title, created_at)
eval_results(id, eval_id, version, session_id, score, passed, metric, unit, reasoning, created_at); evals(id, name, kind, version, enabled)
Timestamps are ISO-8601 UTC strings like '2026-11-25T09:30:00.000Z'."""


def _window(q: str) -> str:
    if re.search(r"\btoday\b", q):
        return "date('now')"
    if re.search(r"\b(yesterday|24 ?h|last day)\b", q):
        return "datetime('now','-1 day')"
    if re.search(r"\bmonth\b", q):
        return "datetime('now','-30 days')"
    if re.search(r"\b(week|7 days)\b", q):
        return "datetime('now','-7 days')"
    return "datetime('now','-3650 days')"


def template_sql(question: str) -> str:
    q = question.lower()
    since = _window(q)
    if re.search(r"token", q):
        return ("SELECT m.session_id, s.first_prompt, SUM(m.tokens_in+m.tokens_out+m.cache_read+m.cache_write) AS tokens, "
                f"ROUND(SUM(m.cost_usd),4) AS cost_usd FROM model_calls m LEFT JOIN sessions s ON s.id=m.session_id "
                f"WHERE m.ts >= {since} GROUP BY m.session_id ORDER BY tokens DESC LIMIT 10")
    if re.search(r"\b(cost|spend|spent|expensive|money|\$)", q):
        by = "model" if "model" in q else "session_id"
        return (f"SELECT {by}, ROUND(SUM(cost_usd),4) AS cost_usd, COUNT(*) AS calls FROM model_calls "
                f"WHERE ts >= {since} GROUP BY {by} ORDER BY cost_usd DESC LIMIT 10")
    if re.search(r"\b(denied|deny|denies|blocked|block|instruct|policy|policies|rule)", q):
        dec = "'instruct'" if "instruct" in q else "'deny'"
        return ("SELECT d.policy, COUNT(*) AS hits, COUNT(DISTINCT e.session_id) AS sessions, "
                "GROUP_CONCAT(DISTINCT e.session_id) AS session_ids FROM decisions d JOIN events e ON e.id=d.event_id "
                f"WHERE d.decision={dec} AND e.ts >= {since} GROUP BY d.policy ORDER BY hits DESC LIMIT 15")
    if re.search(r"\b(error|fail|failed|failing)", q):
        return ("SELECT tool_name, COUNT(*) AS errors, GROUP_CONCAT(DISTINCT session_id) AS session_ids FROM events "
                f"WHERE error IS NOT NULL AND ts >= {since} GROUP BY tool_name ORDER BY errors DESC LIMIT 10")
    if re.search(r"\b(slow|latency|fast)", q):
        return ("SELECT event, COUNT(*) n, ROUND(AVG(latency_ms),2) AS avg_ms, ROUND(MAX(latency_ms),2) AS max_ms "
                f"FROM events WHERE ts >= {since} GROUP BY event ORDER BY avg_ms DESC")
    if re.search(r"\b(frustrat|angry|sentiment|mood)", q):
        return ("SELECT r.session_id, r.score, r.passed FROM eval_results r JOIN evals e ON e.id=r.eval_id "
                f"WHERE e.name='sentiment' AND r.created_at >= {since} ORDER BY r.score ASC LIMIT 10")
    if re.search(r"\b(tool|command|bash|read|edit)", q):
        return ("SELECT tool_name, COUNT(*) AS calls, SUM(decision='deny') AS denied FROM events "
                f"WHERE event='PreToolUse' AND ts >= {since} GROUP BY tool_name ORDER BY calls DESC")
    return ("SELECT id AS session_id, harness, cwd, first_prompt, last_ts, status FROM sessions "
            f"WHERE last_ts >= {since} ORDER BY last_ts DESC LIMIT 20")


def llm_sql(question: str) -> str:
    from ..llm import complete
    text = complete("You translate questions about an AI-agent activity log into ONE SQLite SELECT statement. "
                    "Schema:\n" + SCHEMA + "\nReturn only the SQL, no prose. Include session ids when relevant. "
                    "LIMIT 50.", question, timeout=20)
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip().rstrip(";")


def _session_ids(cols: list[str], out: list[list]) -> list[str]:
    ids: list[str] = []
    for i, c in enumerate(cols):
        if c in ("session_id", "session_ids", "id") or c.endswith("session_id"):
            for r in out:
                for v in str(r[i] or "").split(","):
                    v = v.strip()
                    if v and v not in ids:
                        ids.append(v)
    return ids[:50]


_AUDIT_Q = re.compile(r"(?i)\bwhy\b.*\b(fail\w*|break\w*|wrong|stuck|loop\w*|block\w*|den\w*)\b|root cause|"
                      r"keep(s)? (failing|breaking|looping)|what (goes|went|is going) wrong|biggest (problem|issue)s?")


def audit_answer(conn: sqlite3.Connection, question: str) -> dict:
    """Plain-words audit questions answer from the latest findings + error groups (CLAUDE.md §17.11)."""
    from ..analytics.errors import groups
    sql = ("SELECT failure_mode, severity, count, session_ids FROM findings WHERE audit_id = (SELECT audit_id FROM "
           "findings ORDER BY created_at DESC LIMIT 1) ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 "
           "ELSE 2 END, count DESC LIMIT 10")
    cols, out = safe_select(conn, sql, timeout_s=5.0, max_rows=50)
    notes = []
    if not out:
        notes.append("No audit findings yet: run `dinoguard audit` (or ask an agent to use the MCP run_audit tool, "
                     "which needs your approval).")
    for f in out[:3]:
        notes.append(f"{f[0]} ({f[1]}, {f[2]} hits)")
    for g in groups(conn, days=7, limit=3):
        notes.append(f"repeated error x{g['count']} in {len(g['sessions'])} session(s): {g['pattern'][:100]}")
    notes.append("Findings are evidence for review, not proof.")
    ids = _session_ids(cols, [[str(r[3] or "").strip("[]").replace('"', "") if i == 3 else v
                               for i, v in enumerate(r)] for r in out])
    return {"sql": sql, "method": "audit", "columns": cols, "rows": out, "session_ids": ids, "notes": notes}


def ask(conn: sqlite3.Connection, question: str) -> dict:
    if _AUDIT_Q.search(question or ""):
        return audit_answer(conn, question)
    method = "template"
    sql = None
    try:
        sql = llm_sql(question)
        check_select(sql)
        method = "llm"
    except Exception:  # noqa: BLE001 - no LLM / bad SQL: fall back to templates
        sql = template_sql(question)
    try:
        cols, out = safe_select(conn, sql, timeout_s=5.0, max_rows=200)
    except QueryError as e:
        if method == "llm":
            sql, method = template_sql(question), "template"
            cols, out = safe_select(conn, sql, timeout_s=5.0, max_rows=200)
        else:
            return {"sql": sql, "method": method, "error": str(e), "columns": [], "rows": [], "session_ids": []}
    return {"sql": sql, "method": method, "columns": cols, "rows": out, "session_ids": _session_ids(cols, out)}
