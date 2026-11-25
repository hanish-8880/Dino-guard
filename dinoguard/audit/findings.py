"""Group raw policy hits into findings (failure mode, severity, evidence, affected sessions)."""
from __future__ import annotations

import json
import sqlite3

from ..util import new_id, now_iso

HIGH_CATEGORIES = {"self-protection", "secrets", "dangerous", "environment", "infrastructure", "git", "database"}
MODE_LABEL = {
    "loop-detected": "agent looping on the same call",
    "secret-in-output": "secrets exposed in tool output",
    "secret-before-write": "secrets written into files",
    "token-repeat-read": "token waste: re-reading the same file",
    "token-big-output": "token waste: huge tool outputs",
    "goal-drift": "drifting away from the user's task",
    "destructive-command": "destructive commands",
}


def severity(policy, decision: str) -> str:
    cat = policy.category if policy else ""
    if cat == "tokens":
        return "low"
    if decision == "deny" and cat in HIGH_CATEGORIES:
        return "high"
    if decision == "deny":
        return "medium"
    return "medium" if cat in HIGH_CATEGORIES else "low"


def group_findings(hits: list[dict], registry) -> list[dict]:
    groups: dict[str, dict] = {}
    for h in hits:
        g = groups.setdefault(h["policy"], {"policy": h["policy"], "decisions": {}, "hits": []})
        g["hits"].append(h)
        g["decisions"][h["decision"]] = g["decisions"].get(h["decision"], 0) + 1
    out = []
    rank = {"high": 0, "medium": 1, "low": 2}
    for name, g in groups.items():
        p = registry.get(name) if registry else None
        top = "deny" if "deny" in g["decisions"] else "instruct"
        sev = severity(p, top)
        sessions = sorted({h["session"] for h in g["hits"]})
        out.append({"policy": name, "failure_mode": MODE_LABEL.get(name) or (p.description if p else name),
                    "severity": sev, "count": len(g["hits"]), "session_ids": sessions,
                    "evidence": [{k: h[k] for k in ("session", "ts", "tool", "summary", "reason", "project")}
                                 for h in g["hits"][:5]]})
    out.sort(key=lambda f: (rank[f["severity"]], -f["count"]))
    return out


def store_findings(conn: sqlite3.Connection, audit_id: str, findings: list[dict]) -> None:
    conn.execute("DELETE FROM findings WHERE audit_id=?", (audit_id,))
    for f in findings:
        fid = new_id("fnd")
        f["id"] = fid
        conn.execute("INSERT INTO findings(id, audit_id, failure_mode, severity, evidence_json, session_ids, count, "
                     "created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (fid, audit_id, f"{f['policy']}: {f['failure_mode']}", f["severity"], json.dumps(f["evidence"]),
                      json.dumps(f["session_ids"]), f["count"], now_iso()))
        if f["severity"] == "high":
            exists = conn.execute("SELECT 1 FROM insights WHERE source='audit' AND title=? AND status!='resolved'",
                                  (f"Audit: {f['failure_mode']}",)).fetchone()
            if not exists:
                conn.execute("INSERT INTO insights(id, source, finding_id, status, title, created_at) VALUES "
                             "(?,?,?,?,?,?)", (new_id("ins"), "audit", fid, "open", f"Audit: {f['failure_mode']}",
                                               now_iso()))
