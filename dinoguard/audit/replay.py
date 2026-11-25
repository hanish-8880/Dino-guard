"""Local audit: replay harness transcripts on disk through the built-in policies (observe-only).

Output is evidence for review, not proof.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from .. import paths
from ..adapters.claude_code import map_tool
from ..engine.context import History
from ..engine.events import CanonicalEvent, make_tool
from ..engine.registry import Registry
from ..engine.runner import evaluate
from ..secrets.scanner import redact
from ..store.db import rows
from ..store.writer import summarize
from ..util import new_id, now_iso
from .findings import group_findings, store_findings

SKIP_IN_AUDIT = {"tool-not-found", "use-repo-package-manager"}   # depend on today's disk, not history


class NullProbe:
    """Audits replay old sessions: there is no live repo state to probe."""

    def git(self, cwd, *a):
        return (1, "")

    def gh(self, cwd, *a):
        return (1, "")

    def branch(self, cwd):
        return None

    def exists(self, p):
        return True

    def listdir(self, p):
        return []


def transcript_files(days: int) -> list[Path]:
    root = paths.claude_projects_dir()
    if not root.is_dir():
        return []
    cutoff = time.time() - days * 86400
    return sorted(p for p in root.rglob("*.jsonl") if p.stat().st_mtime >= cutoff)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in content)
    return ""


def parse_transcript(path: Path) -> list[CanonicalEvent]:
    """Claude Code JSONL -> canonical UserPromptSubmit / PreToolUse / PostToolUse events."""
    out: list[CanonicalEvent] = []
    pending: dict[str, CanonicalEvent] = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            sid = obj.get("sessionId") or path.stem
            sess = {"id": sid, "cwd": obj.get("cwd"), "transcript_path": str(path)}
            ts = obj.get("timestamp") or now_iso()
            msg = obj.get("message") or {}
            content = msg.get("content")
            if obj.get("type") == "user":
                if isinstance(content, str) and content.strip():
                    out.append(CanonicalEvent(event="UserPromptSubmit", harness="claude", session=sess, prompt=content, ts=ts))
                    continue
                for c in content if isinstance(content, list) else []:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "tool_result" and c.get("tool_use_id") in pending:
                        pre = pending.pop(c["tool_use_id"])
                        text = _text(c.get("content"))
                        out.append(CanonicalEvent(event="PostToolUse", harness="claude", session=sess, tool=pre.tool,
                                                  output=text[:16000], output_chars=len(text), ts=ts,
                                                  error=text[:500] if c.get("is_error") else None,
                                                  tool_use_id=c["tool_use_id"]))
                    elif c.get("type") == "text" and not obj.get("isMeta") and c.get("text", "").strip():
                        out.append(CanonicalEvent(event="UserPromptSubmit", harness="claude", session=sess,
                                                  prompt=c["text"], ts=ts))
            elif obj.get("type") == "assistant":
                for c in content if isinstance(content, list) else []:
                    if isinstance(c, dict) and c.get("type") == "tool_use":
                        name, inp = map_tool(c.get("name"), c.get("input"))
                        ev = CanonicalEvent(event="PreToolUse", harness="claude", session=sess, tool=make_tool(name, inp),
                                            ts=ts, tool_use_id=c.get("id"))
                        pending[c.get("id")] = ev
                        out.append(ev)
    return out


def run_audit(conn: sqlite3.Connection, days: int = 30, registry: Registry | None = None) -> dict:
    reg = registry or Registry().load_builtins()
    reg.policies = [p for p in reg.policies if p.name not in SKIP_IN_AUDIT]
    files = transcript_files(days)
    hits: list[dict] = []
    sessions, tool_calls, projects = set(), 0, set()
    probe = NullProbe()
    for f in files:
        histories: dict[str, list] = {}
        prompts: dict[str, list] = {}
        for ev in parse_transcript(f):
            sid = ev.session_id
            sessions.add(sid)
            if ev.session.get("cwd"):
                projects.add(ev.session["cwd"])
            items = histories.setdefault(sid, [])
            ps = prompts.setdefault(sid, [])
            if ev.event == "UserPromptSubmit":
                ps.append(ev.prompt)
            if ev.event == "PreToolUse":
                tool_calls += 1
            hist = History(items, first_prompt=ps[0] if ps else None, prompts=ps)
            result = evaluate(ev, reg, {}, history=hist, probe=probe, audit=True)
            decs = []
            for r in result.results:
                if r.decision.kind == "allow":
                    continue
                decs.append({"policy": r.policy, "decision": r.decision.kind})
                hits.append({"policy": r.policy, "decision": r.decision.kind, "session": sid, "ts": ev.ts,
                             "project": ev.session.get("cwd") or "?", "tool": ev.tool_name, "event": ev.event,
                             "summary": redact(summarize(ev))[:200], "reason": (r.decision.reason or "")[:300]})
            items.append({"event": ev.event, "tool_name": ev.tool_name, "input": ev.tool_input, "error": ev.error,
                          "decisions": decs})
    findings = group_findings(hits, reg)
    audit_id = new_id("aud")
    scope = {"days": days, "files": len(files), "sessions": len(sessions), "tool_calls": tool_calls,
             "projects": sorted(projects), "hits": len(hits),
             "by_project": _count(hits, "project"), "by_policy": _count(hits, "policy")}
    existing = conn.execute("SELECT id FROM audits WHERE cadence_days IS NOT NULL LIMIT 1").fetchone()
    if existing:
        audit_id = existing["id"]
        conn.execute("UPDATE audits SET last_run=?, scope_json=? WHERE id=?", (now_iso(), json.dumps(scope), audit_id))
    else:
        conn.execute("INSERT INTO audits(id, goal, scope_json, last_run) VALUES (?,?,?,?)",
                     (audit_id, "local history replay", json.dumps(scope), now_iso()))
    store_findings(conn, audit_id, findings)
    conn.commit()
    return {"audit_id": audit_id, "files": len(files), "sessions": len(sessions), "tool_calls": tool_calls,
            "hits": len(hits), "findings": findings}


def _count(hits, key) -> dict:
    out: dict[str, int] = {}
    for h in hits:
        out[h[key]] = out.get(h[key], 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def overview(conn: sqlite3.Connection) -> dict:
    r = rows(conn, "SELECT scope_json FROM audits WHERE last_run IS NOT NULL ORDER BY last_run DESC LIMIT 1")
    s = json.loads(r[0]["scope_json"]) if r else {}
    return {"sessions": s.get("sessions", 0), "tool_calls": s.get("tool_calls", 0), "policy_hits": s.get("hits", 0),
            "projects": [{"project": os.path.basename(str(k).rstrip("/\\")) or k, "hits": v}
                         for k, v in (s.get("by_project") or {}).items()],
            "by_policy": [{"policy": k, "n": v} for k, v in (s.get("by_policy") or {}).items()]}
