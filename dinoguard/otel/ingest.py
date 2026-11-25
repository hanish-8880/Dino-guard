"""Optional OpenTelemetry ingest (OTLP/JSON over HTTP at POST /api/v1/traces on the dashboard).

Spans become observe-only events (harness "otel"). GenAI semantic-convention attributes
(gen_ai.request.model, gen_ai.usage.input_tokens/output_tokens) become model_calls rows.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..secrets.scanner import redact_obj
from ..util import dumps, new_id


def _attrs(span: dict) -> dict:
    out = {}
    for a in span.get("attributes") or []:
        v = a.get("value") or {}
        out[a.get("key")] = next(iter(v.values()), None) if isinstance(v, dict) else v
    return out


def _ts(nanos) -> str:
    try:
        return datetime.fromtimestamp(int(nanos) / 1e9, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def ingest_otlp_json(conn, payload: dict) -> int:
    n = 0
    for rs in payload.get("resourceSpans") or []:
        res = {a.get("key"): next(iter((a.get("value") or {}).values()), None) for a in
               (rs.get("resource") or {}).get("attributes") or []}
        service = res.get("service.name") or "otel"
        for ss in rs.get("scopeSpans") or []:
            for sp in ss.get("spans") or []:
                at = _attrs(sp)
                sid = str(at.get("session.id") or at.get("gen_ai.conversation.id") or sp.get("traceId") or "otel")
                ts = _ts(sp.get("startTimeUnixNano"))
                dur = None
                if sp.get("endTimeUnixNano") and sp.get("startTimeUnixNano"):
                    dur = int((int(sp["endTimeUnixNano"]) - int(sp["startTimeUnixNano"])) / 1e6)
                if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
                    conn.execute("INSERT INTO sessions(id, harness, started_at, status, last_ts) VALUES (?,?,?,?,?)",
                                 (sid, f"otel:{service}", ts, "active", ts))
                conn.execute("UPDATE sessions SET last_ts=MAX(COALESCE(last_ts,''), ?) WHERE id=?", (ts, sid))
                model = at.get("gen_ai.request.model") or at.get("gen_ai.response.model")
                if model or at.get("gen_ai.usage.input_tokens") is not None:
                    from ..tokens.prices import cost
                    ti, to = int(at.get("gen_ai.usage.input_tokens") or 0), int(at.get("gen_ai.usage.output_tokens") or 0)
                    conn.execute("INSERT OR REPLACE INTO model_calls(id, session_id, ts, model, tokens_in, tokens_out, "
                                 "cache_read, cost_usd, latency_ms, context_tokens) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (f"otel_{sp.get('spanId') or new_id()}", sid, ts, model, ti, to, 0, cost(model, ti, to),
                                  dur, ti))
                else:
                    tool = at.get("gen_ai.tool.name") or at.get("tool.name")
                    err = (sp.get("status") or {}).get("code") in (2, "STATUS_CODE_ERROR")
                    conn.execute("INSERT OR REPLACE INTO events(id, session_id, ts, event, tool_name, tool_class, "
                                 "input_json, duration_ms, error, harness, decision) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                 (f"otel_{sp.get('spanId') or new_id()}", sid, ts,
                                  "PostToolUse" if tool else "Notification", tool or sp.get("name"), "other",
                                  dumps(redact_obj({"span": sp.get("name"), **{k: v for k, v in at.items()
                                                                              if isinstance(v, (str, int, float))}})),
                                  dur, (sp.get("status") or {}).get("message") or ("error" if err else None),
                                  f"otel:{service}", "allow"))
                n += 1
    conn.commit()
    return n


__all__ = ["ingest_otlp_json", "json"]
