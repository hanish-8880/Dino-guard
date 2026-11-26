"""Local audit: replay harness transcripts on disk through the built-in policies (observe-only).

Output is evidence for review, not proof.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
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
        for lineno, line in enumerate(f, 1):
            mark = {"_line": lineno}
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
                    out.append(CanonicalEvent(event="UserPromptSubmit", harness="claude", session=sess, prompt=content, ts=ts,
                                              raw=mark))
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
                                                  tool_use_id=c["tool_use_id"], raw=mark))
                    elif c.get("type") == "text" and not obj.get("isMeta") and c.get("text", "").strip():
                        out.append(CanonicalEvent(event="UserPromptSubmit", harness="claude", session=sess,
                                                  prompt=c["text"], ts=ts, raw=mark))
            elif obj.get("type") == "assistant":
                for c in content if isinstance(content, list) else []:
                    if isinstance(c, dict) and c.get("type") == "tool_use":
                        name, inp = map_tool(c.get("name"), c.get("input"))
                        ev = CanonicalEvent(event="PreToolUse", harness="claude", session=sess, tool=make_tool(name, inp),
                                            ts=ts, tool_use_id=c.get("id"), raw=mark)
                        pending[c.get("id")] = ev
                        out.append(ev)
    return out


def _selected(ev, project: str | None, sessions: list[str] | None, env_ids: set[str] | None) -> bool:
    sid = ev.session_id
    if sessions and not any(sid == x or sid.startswith(x) for x in sessions):
        return False
    if project and project.lower() not in str(ev.session.get("cwd") or "").replace("\\", "/").lower():
        return False
    if env_ids is not None and sid not in env_ids:
        return False
    return True


def run_audit(conn: sqlite3.Connection, days: int = 30, registry: Registry | None = None, goal: str | None = None,
              project: str | None = None, sessions: list[str] | None = None, env: str | None = None,
              context: str | None = None, notify: bool = True) -> dict:
    """Replay history (optionally only a selection) through the built-ins + the agent contract."""
    from . import contract as contract_mod
    from .runs import check_recurrence, coverage, record_run
    started = now_iso()
    reg = registry or Registry().load_builtins()
    reg.policies = [p for p in reg.policies if p.name not in SKIP_IN_AUDIT]
    files = transcript_files(days)
    env_ids = ({r["id"] for r in rows(conn, "SELECT id FROM sessions WHERE environment=?", (env,))}
               if env else None)
    hits: list[dict] = []
    scanned, tool_calls, projects = set(), 0, set()
    by_project: dict[str, set] = {}
    review: dict[str, list[str]] = {}
    probe = NullProbe()
    for f in files:
        histories: dict[str, list] = {}
        prompts: dict[str, list] = {}
        for ev in parse_transcript(f):
            if not _selected(ev, project, sessions, env_ids):
                continue
            sid = ev.session_id
            cwd = ev.session.get("cwd")
            scanned.add(sid)
            if cwd:
                projects.add(cwd)
                by_project.setdefault(cwd, set()).add(sid)
            items = histories.setdefault(sid, [])
            ps = prompts.setdefault(sid, [])
            if ev.event == "UserPromptSubmit":
                ps.append(ev.prompt)
            base = {"session": sid, "ts": ev.ts, "project": cwd or "?", "tool": ev.tool_name, "event": ev.event,
                    "where": f"{f}:{ev.raw.get('_line', '?')}"}
            if ev.event == "PreToolUse":
                tool_calls += 1
                c = contract_mod.load(cwd)
                if c and (cwd or "?") not in review:
                    review[cwd or "?"] = contract_mod.review_items(c)
                for rule in contract_mod.violations(c, ev.tool_name, ev.tool_input):
                    hits.append({**base, "policy": f"contract:{rule['text']}", "decision": "deny",
                                 "summary": redact(summarize(ev))[:200],
                                 "reason": f"contract: should not {rule['text']}"})
            hist = History(items, first_prompt=ps[0] if ps else None, prompts=ps)
            result = evaluate(ev, reg, {}, history=hist, probe=probe, audit=True)
            decs = []
            for r in result.results:
                if r.decision.kind == "allow":
                    continue
                decs.append({"policy": r.policy, "decision": r.decision.kind})
                hits.append({**base, "policy": r.policy, "decision": r.decision.kind,
                             "summary": redact(summarize(ev))[:200], "reason": (r.decision.reason or "")[:300]})
            items.append({"event": ev.event, "tool_name": ev.tool_name, "input": ev.tool_input, "error": ev.error,
                          "decisions": decs})
    findings = group_findings(hits, reg)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cov = coverage(conn, scanned, since, by_project)
    selection = {"project": project, "sessions": sessions, "env": env}
    scope = {"days": days, "files": len(files), "sessions": len(scanned), "tool_calls": tool_calls,
             "projects": sorted(projects), "hits": len(hits), "goal": goal, "selection": selection,
             "context": _context_text(context), "needs_review": review, "coverage": cov,
             "by_project": _count(hits, "project"), "by_policy": _count(hits, "policy")}
    audit_id = new_id("aud")
    custom = bool(goal or project or sessions or env)
    existing = None if custom else conn.execute("SELECT id FROM audits WHERE cadence_days IS NOT NULL LIMIT 1").fetchone()
    if existing:
        audit_id = existing["id"]
        conn.execute("UPDATE audits SET last_run=?, scope_json=? WHERE id=?", (now_iso(), json.dumps(scope), audit_id))
    else:
        conn.execute("INSERT INTO audits(id, goal, scope_json, last_run) VALUES (?,?,?,?)",
                     (audit_id, goal or "local history replay", json.dumps(scope), now_iso()))
    store_findings(conn, audit_id, findings)
    res = {"audit_id": audit_id, "files": len(files), "sessions": len(scanned), "tool_calls": tool_calls,
           "hits": len(hits), "findings": findings, "goal": goal, "coverage": cov, "needs_review": review}
    res["run_id"] = record_run(conn, audit_id, started, res, cov, goal, selection)
    conn.commit()
    res["recurred"] = check_recurrence(conn, findings, notify=notify)
    return res


def _context_text(context: str | None) -> str | None:
    """Reference context: a file path (first 4 KB is kept) or literal text."""
    if not context:
        return None
    p = Path(context)
    try:
        if p.is_file():
            return redact(p.read_text(encoding="utf-8", errors="replace")[:4096])
    except OSError:
        pass
    return redact(context[:4096])


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
