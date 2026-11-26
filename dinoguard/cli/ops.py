"""runner / dashboard / audit / eval / alerts / approve / channel / reviewer / ask / mcp / publish / demo."""
from __future__ import annotations

import json
import sys

from rich.console import Console
from rich.table import Table

from .. import config

console = Console(highlight=False)


def _kv(pairs):
    out = {}
    for kv in pairs or []:
        k, _, v = kv.partition("=")
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out


def cmd_runner(args) -> int:
    if args.web:
        import webbrowser
        webbrowser.open("http://127.0.0.1:8030/runner")
        console.print("Opening http://127.0.0.1:8030/runner (start `dinoguard dashboard` if it is not running)")
        return 0
    from ..runner_ui.app import run
    return run(fps=args.fps)


def cmd_notch(args) -> int:
    from ..runner_ui import notch_model, notch_os
    if args.no_autostart:
        removed = notch_os.disable_autostart()
        console.print("✔ notch will no longer open on login" if removed else "notch was not set to open on login")
        return 0
    if args.corner:
        notch_model.save_pos({"corner": args.corner})
    if args.autostart:
        console.print(f"✔ notch opens on login: {notch_os.enable_autostart()}")
    if args.foreground:
        from ..runner_ui.notch import run
        return run(fps=args.fps, max_seconds=args.seconds)
    probe = notch_os.single_instance()
    if probe is None:
        console.print("dino notch is already running (right-click it → Quit to close).")
        return 0
    probe.close()
    pid = notch_os.spawn_detached()
    console.print(f"✔ dino notch started (pid {pid}). Drag it anywhere; right-click for corner / dashboard / "
                  "hide / quit.")
    return 0


def cmd_dashboard(args) -> int:
    from ..dashboard.app import serve
    return serve(port=args.port, open_browser=not args.no_open)


def cmd_audit(args) -> int:
    from ..audit import schedule
    from ..store import db
    from . import data
    if args.history or args.action == "history":
        return data.audit_history()
    if args.action == "suggest":
        if not args.finding:
            console.print("usage: dinoguard audit suggest <finding-id> [--install]")
            return 2
        return data.audit_suggest(args.finding, args.install)
    conn = db.connect()
    if args.status:
        for a in schedule.list_audits(conn):
            console.print(f" {a['id']} every {a['cadence_days']}d last={a['last_run']} next={a['next_run']} "
                          f"email={a.get('email') or '-'}")
        return 0
    if args.no_schedule:
        schedule.unschedule(conn)
        console.print("[green]✔[/] audit schedule removed")
        return 0
    if args.schedule is not None:
        if not 1 <= args.schedule <= 90:
            console.print("[red]--schedule must be 1-90 days[/]")
            return 2
        schedule.set_schedule(conn, args.schedule, args.email)
        console.print(f"[green]✔[/] audit scheduled every {args.schedule} days (runs from the daemon)")
    from ..audit.replay import run_audit
    res = run_audit(conn, days=args.days, goal=args.goal, project=args.project, sessions=args.session,
                    env=args.env, context=args.context)
    if res.get("goal"):
        console.print(f"[bold]Goal:[/] {res['goal']}")
    console.print(f"[bold]Audit[/] scanned {res['sessions']} sessions, {res['tool_calls']} tool calls "
                  f"from {res['files']} transcript files.")
    cov = res.get("coverage") or {}
    console.print(f"Coverage: {cov.get('store_sessions_covered', 0)} of {cov.get('store_sessions', 0)} sessions in "
                  "the DinoGuard store were replayed.")
    console.print("[dim]Findings are evidence for review, not proof.[/]")
    t = Table("finding id", "severity", "failure mode", "count", "sessions", "example")
    for f in res["findings"]:
        ex = (f["evidence"][0]["summary"] if f["evidence"] else "")[:60]
        t.add_row(f.get("id", ""), f["severity"], f["failure_mode"], str(f["count"]), str(len(f["session_ids"])), ex)
    console.print(t)
    for f in res["findings"][:3]:
        if f["evidence"] and f["evidence"][0].get("where"):
            console.print(f"[dim]  {f['policy']}: {f['evidence'][0]['where']}[/]")
    for proj, items in (res.get("needs_review") or {}).items():
        for it in items:
            console.print(f"[yellow]needs human review[/] ({proj}): {it}")
    for r in res.get("recurred") or []:
        console.print(f"[red]recurred[/] {r['title']} (insight {r['insight']} reopened)")
    if res["findings"]:
        console.print("Draft a policy from a finding: dinoguard audit suggest <finding-id> --install")
    if args.email:
        from ..audit.schedule import email_report
        email_report(res, args.email)
    return 0


def cmd_eval(args) -> int:
    from ..evals import manage
    from ..store import db
    conn = db.connect()
    if args.action == "new":
        spec = args.spec or ""
        if args.from_text and not spec:
            from ..evals.draft import draft_spec
            d = draft_spec(args.from_text)
            spec = d["spec"]
            console.print(f"[dim]drafted ({d['method']}):[/] {spec}")
        e = manage.new(conn, args.name, args.kind, spec, args.condition)
        console.print(f"[green]✔[/] eval draft {e['name']} ({e['kind']}). Next: dinoguard eval test {e['name']}")
    elif args.action == "test":
        for r in manage.test(conn, args.name, limit=args.limit):
            console.print(f" {r['session_id'][:12]} score={r['score']} passed={r['passed']} {r.get('reasoning') or ''}")
    elif args.action == "deploy":
        v = manage.deploy(conn, args.name)
        console.print(f"[green]✔[/] deployed {args.name} v{v}; scores new sessions as they end")
    elif args.action == "backfill":
        n = manage.backfill(conn, args.name, limit=args.limit)
        console.print(f"[green]✔[/] scored {n} past sessions")
    elif args.action == "rollback":
        v = manage.rollback(conn, args.name)
        console.print(f"[green]✔[/] {args.name} rolled back to the code of v{v}")
    elif args.action == "results" and args.by:
        t = Table(args.by, "results", "avg score", "pass rate")
        for r in manage.compare(conn, args.name, args.by):
            t.add_row(str(r["grp"]), str(r["n"]), str(r["avg_score"]), str(r["pass_rate"]))
        console.print(t)
    elif args.action == "results":
        for r in manage.results(conn, args.name, limit=args.limit):
            console.print(f" {r['created_at']} {r['session_id'][:12]} v{r['version']} score={r['score']} "
                          f"passed={r['passed']}")
    else:
        for e in manage.list_evals(conn):
            console.print(f" {e['name']} {e['kind']} v{e['version']} {'on' if e['enabled'] else 'draft'}")
    return 0


def cmd_alerts(args) -> int:
    from ..alerts import engine
    from ..store import db
    conn = db.connect()
    if args.action == "new":
        a = engine.create_from_text(conn, args.text or "", channels=args.channel or [], severity=args.severity)
        prev = engine.preview(conn, a["id"])
        console.print(f"[green]✔[/] alert {a['id']} '{a['name']}' ({a['trigger_kind']} {a['trigger_spec']})")
        console.print(f"   would have fired {prev['would_fire']} times in the last 24h")
    elif args.action == "test":
        res = engine.test_notification(conn, args.text)
        console.print(f"sent test to: {', '.join(res['sent']) or '-'}; errors: {res['errors'] or '-'}")
    elif args.action == "check":
        fired = engine.check_all(conn, config.load(), force=True)
        console.print(f"checked; fired {len(fired)}")
    elif args.action == "delete":
        engine.delete(conn, args.text)
        console.print("[green]✔[/] deleted")
    else:
        for a in engine.list_alerts(conn):
            console.print(f" {a['id']} {'on ' if a['enabled'] else 'off'} {a['severity']:<8} {a['name']}")
    return 0


def cmd_approve(args) -> int:
    from ..approvals import flow, setup
    from ..store import db
    conn = db.connect()
    if args.action == "setup":
        kind = "whatsapp" if args.whatsapp else "telegram" if args.telegram else None
        if not kind:
            console.print("[red]choose --whatsapp or --telegram[/]")
            return 2
        setup.interactive(kind, provider=args.provider, values=_kv(args.args))
        console.print(f"[green]✔[/] approvals will be sent via {kind}. Enable: dinoguard policies enforce phone-approval")
    elif args.action == "answer":
        if len(args.args) != 2:
            console.print("usage: dinoguard approve answer <code> yes|no")
            return 2
        ok = flow.answer(conn, args.args[0], args.args[1].lower() in ("y", "yes", "approve"))
        console.print("[green]✔[/] answered" if ok else "[yellow]no pending approval with that code[/]")
    elif args.action == "test":
        from ..alerts.channels import send
        ch = config.load()["approvals"].get("channel")
        send(ch, "DinoGuard test: approvals are wired up.")
        console.print(f"[green]✔[/] sent to {ch}")
    else:
        for p in flow.pending(conn):
            console.print(f" #{p['code']} {p['sent_at']} {p['summary']}")
    return 0


def cmd_channel(args) -> int:
    from ..approvals.setup import save_channel
    save_channel(args.name, args.type, _kv(args.settings))
    console.print(f"[green]✔[/] channel {args.name} ({args.type}) saved")
    return 0


def cmd_reviewer(args) -> int:
    from ..reviewer import review
    if args.action == "setup":
        key = None
        if args.key_stdin:
            if sys.stdin.isatty():         # typed/pasted at a terminal: say so, and don't echo the key
                import getpass
                key = getpass.getpass("Paste the API key (hidden), then press Enter: ").strip()
            else:                          # piped in
                key = sys.stdin.readline().strip()
        review.setup(url=args.url, model=args.model, mode=args.mode, key=key or None)
        console.print(f"[green]✔[/] reviewer configured: {config.load()['reviewer']}")
        if key:
            console.print(f"[green]✔[/] API key saved ({len(key)} characters, stored in the DinoGuard secrets file, "
                          "never shown)")
        elif args.key_stdin:
            console.print("[yellow]![/] no key was entered, so none was saved. Run the same command again and paste "
                          "the key at the prompt.")
        if not review.status()["has_key"]:
            console.print("[yellow]![/] the reviewer has no API key yet; calls will fail until you add one.")
    elif args.action == "status":
        console.print_json(json.dumps(review.status()))
    else:
        console.print_json(json.dumps(review.self_test()))
    return 0


def cmd_ask(args) -> int:
    from ..assistant.nl2sql import ask
    from ..store import db
    res = ask(db.connect(), " ".join(args.question))
    console.print(f"[dim]SQL ({res['method']}):[/] {res['sql']}")
    if res.get("error"):
        console.print(f"[red]{res['error']}[/]")
        return 1
    t = Table(*res["columns"])
    for r in res["rows"][:50]:
        t.add_row(*[str(x) for x in r])
    console.print(t)
    for note in res.get("notes") or []:
        console.print(f"[yellow]•[/] {note}")
    if res.get("session_ids"):
        console.print("sessions: " + ", ".join(res["session_ids"][:10]))
    console.print("[dim]Answers are investigation aids; check the SQL.[/]")
    return 0


def cmd_mcp(args) -> int:
    if args.http:
        from ..assistant.mcp_http import serve_http
        return serve_http(args.port)
    from ..assistant.mcp_server import serve_stdio
    return serve_stdio()


def cmd_publish(args) -> int:
    from ..packs.build import build_pack
    res = build_pack(args.name, args.publisher, args.out)
    console.print(f"[green]✔[/] built pack {res['name']} with {len(res['policies'])} policies in {res['out']}")
    console.print("Upload the files in that folder as assets of a GitHub release.")
    return 0


def cmd_demo(args) -> int:
    from ..runner_ui.demo import run_demo
    return run_demo(seconds=args.seconds, show_runner=not args.no_runner)
