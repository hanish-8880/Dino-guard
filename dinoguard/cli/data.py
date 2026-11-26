"""prune / export / compare / errors / audit extras (CLAUDE.md §17)."""
from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .. import config

console = Console(highlight=False)


def _conn():
    from ..store import db
    return db.connect()


def cmd_prune(args) -> int:
    from ..store.retention import prune
    days = args.days if args.days is not None else int(config.load().get("retention_days") or 0)
    if days <= 0:
        console.print("retention is off (retention_days = 0). Pass --days N to prune anyway.")
        return 0
    res = prune(_conn(), days, dry_run=args.dry_run)
    verb = "would delete" if args.dry_run else "deleted"
    parts = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in res.items() if isinstance(v, int) and v)
    console.print(f"[green]✔[/] {verb} data older than {days} days (before {res['cutoff']}): {parts or 'nothing'}")
    return 0


def cmd_export(args) -> int:
    from ..analytics.export import export_session, to_jsonl
    doc = export_session(_conn(), args.session)
    text = to_jsonl(doc) if args.format == "jsonl" else json.dumps(doc, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        console.print(f"[green]✔[/] exported {doc['session']['id']} ({doc['counts']['events']} events) to {args.out}")
    else:
        print(text)
    return 0


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def cmd_compare(args) -> int:
    from ..analytics import compare
    conn = _conn()
    if args.by:
        rows = compare.compare_groups(conn, args.by, days=args.days)
        if not rows:
            console.print(f"no sessions in the last {args.days} days")
            return 0
        t = Table("metric", *[str(r["group"])[:24] for r in rows], title=f"sessions by {args.by}, last {args.days} days")
        for c in ["sessions", "clean_rate"] + [f"avg_{k}" for k in compare.METRICS]:
            t.add_row(c.replace("avg_", "avg "), *[_fmt(r[c]) for r in rows])
        console.print(t)
        return 0
    if len(args.sessions) != 2:
        console.print("usage: dinoguard compare <session-a> <session-b>   or   dinoguard compare --by model")
        return 2
    res = compare.compare_two(conn, *args.sessions)
    a, b = res["a"], res["b"]
    t = Table("metric", a["id"][:12], b["id"][:12], "b - a")
    for k in ("harness", "model", "environment"):
        t.add_row(k, str(a[k]), str(b[k]), "")
    for k in compare.METRICS:
        t.add_row(k, _fmt(a[k]), _fmt(b[k]), _fmt(res["delta"][k]))
    for name in sorted(set(a["evals"]) | set(b["evals"])):
        t.add_row(f"eval {name}", _fmt(a["evals"].get(name, "-")), _fmt(b["evals"].get(name, "-")), "")
    console.print(t)
    tt = Table("tool", "a", "b", title="tool calls")
    for r in res["tools"]:
        tt.add_row(r["tool"], str(r["a"]), str(r["b"]))
    console.print(tt)
    return 0


def cmd_errors(args) -> int:
    from ..analytics.errors import groups
    t = Table("count", "tools", "sessions", "last seen", "pattern", title=f"error groups, last {args.days} days")
    for g in groups(_conn(), days=args.days, limit=args.limit):
        t.add_row(str(g["count"]), ", ".join(g["tools"]), str(len(g["sessions"])), g["last_seen"][:19],
                  g["pattern"][:80])
    console.print(t)
    return 0


def cmd_undo(args) -> int:
    from .. import undo
    conn = _conn()
    if not args.event:
        t = Table("time", "event", "tool", "why", "repo", "checkpoint", title="recent checkpoints")
        for c in undo.recent(conn):
            t.add_row(c["ts"][:19], c["event_id"][:16], c["tool_name"] or "?", c["reason"], c["repo"],
                      (c["ref"] or f"HEAD {c['head'] or '?'}")[:12])
        console.print(t)
        return 0
    cp = undo.find(conn, args.event)
    if not cp:
        console.print(f"no checkpoint for event {args.event!r} (only allowed risky actions inside git repos get one)")
        return 1
    console.print(f"[bold]Checkpoint[/] before {cp['tool_name'] or 'the action'} at {cp['ts']} ({cp['reason']})")
    console.print(f"repo {cp['repo']}  ·  checkpoint {cp['ref'] or '-'}  ·  HEAD {cp['head'] or '-'}")
    for line in undo.instructions(cp):
        print("  " + line)          # plain print: commands must not be wrapped
    return 0


def audit_history() -> int:
    from ..audit.runs import history
    t = Table("started", "goal", "sessions", "tool calls", "findings", "coverage")
    for r in history(_conn()):
        cov = r["coverage"]
        t.add_row(r["started_at"][:19], (r["goal"] or "local history replay")[:40], str(r["sessions"]),
                  str(r["tool_calls"]), str(r["findings"]),
                  f"{cov.get('store_sessions_covered', 0)}/{cov.get('store_sessions', 0)} stored sessions")
    console.print(t)
    return 0


def audit_suggest(finding_id: str, install: bool) -> int:
    from ..authoring.draft import draft_from_finding
    conn = _conn()
    d = draft_from_finding(conn, finding_id)
    console.print(f"[green]✔[/] drafted [bold]{d['name']}[/] ({d['method']}) → {d['path']}")
    if not install:
        console.print(f"Next: dinoguard policy test {d['name']}  (or rerun with --install to deploy it in observe)")
        return 0
    from ..deploy import versions
    v = versions.publish(d["name"], notes=f"suggested from finding {finding_id}", conn=conn)
    versions.deploy(d["name"], v, "observe", conn)
    console.print(f"[green]✔[/] installed {d['name']} v{v} in [cyan]observe[/] mode: it records what it would "
                  f"block. Promote later with: dinoguard policies enforce {d['name']}")
    from .pol import _reload_daemon
    _reload_daemon()
    return 0
