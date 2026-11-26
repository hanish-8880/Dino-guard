"""install / uninstall / doctor / status / pause / daemon / first-run wizard."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.table import Table

from .. import config, paths
from ..version import PROTOCOL_VERSION, __version__

console = Console(highlight=False)
OK, WARN, BAD = "[green]✔[/]", "[yellow]![/]", "[red]✘[/]"


def _install_harness(harness: str, scope: str) -> dict:
    if harness in ("claude", "claude-code"):
        from ..adapters.install import install_claude
        return install_claude(scope, os.getcwd())
    from ..adapters.other_install import install_other
    return install_other(harness, scope, os.getcwd())


def cmd_install(args) -> int:
    try:
        res = _install_harness(args.harness, args.scope)
    except ValueError as e:
        console.print(f"{WARN} {e}")
        return 2
    if str(res["settings"]).startswith("(manual"):
        console.print(f"{WARN} {args.harness}: one manual step, then the daemon below does the rest.")
        print(res.get("note") or "")
    else:
        console.print(f"{OK} hooks written to [bold]{res['settings']}[/]")
        console.print(f"   command: [dim]{res['command']}[/]")
        if res.get("note"):
            console.print(f"{WARN} {res['note']}")
    if args.no_daemon:
        config.update(lambda c: c["daemon"].update(enabled=False))
        console.print(f"{OK} in-process mode (no daemon)")
    else:
        from ..daemon.server import is_running, start_detached
        if not is_running(1.0):
            start_detached()
        if is_running(3.0):
            config.update(lambda c: c["daemon"].update(enabled=True))
            console.print(f"{OK} daemon running (fail-closed enabled)")
        else:
            console.print(f"{WARN} daemon did not start; staying in in-process mode. Run `dinoguard doctor`.")
    console.print("Next: [bold]dinoguard runner[/] in a split pane, [bold]dinoguard dashboard[/] for the UI.")
    return 0


def cmd_uninstall(args) -> int:
    if args.harness in ("claude", "claude-code"):
        from ..adapters.install import uninstall_claude
        res = uninstall_claude(args.scope, os.getcwd())
    else:
        from ..adapters.other_install import uninstall_other
        res = uninstall_other(args.harness, args.scope, os.getcwd())
    console.print(f"{OK} {res['action']} ({res['settings']})")
    config.update(lambda c: c["daemon"].update(enabled=False))
    from ..daemon.server import stop
    if stop():
        console.print(f"{OK} daemon stopped")
    return 0


def doctor_checks() -> list[tuple[str, str, str]]:
    """Return [(status, check, detail)] with status in ok|warn|fail."""
    out: list[tuple[str, str, str]] = []
    try:
        h = paths.home()
        (h / ".write-test").write_text("x")
        (h / ".write-test").unlink()
        out.append(("ok", "home", str(h)))
    except OSError as e:
        out.append(("fail", "home", f"not writable: {e}"))
    try:
        from ..store import db
        c = db.connect()
        v = c.execute("PRAGMA user_version").fetchone()[0]
        n = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        out.append(("ok", "store", f"schema v{v}, {n} events"))
    except Exception as e:  # noqa: BLE001
        out.append(("fail", "store", str(e)))
    cfg = config.load()
    from ..daemon import client as dclient
    if (cfg.get("daemon") or {}).get("enabled"):
        try:
            hres = dclient.health()
            skew = hres.get("version") != __version__
            out.append(("warn" if skew else "ok", "daemon",
                        f"pid {hres.get('pid')}, v{hres.get('version')} protocol {hres.get('v')}"
                        + (f" (CLI is v{__version__}: restart the daemon)" if skew else "")))
            for e in hres.get("load_errors") or []:
                out.append(("warn", "policy load", f"{e['file']}: {e['error']}"))
        except dclient.DaemonError as e:
            out.append(("fail", "daemon", f"{e} - every action is being DENIED. Fix: `dinoguard daemon start`"))
    else:
        out.append(("warn", "daemon", "not configured: hooks evaluate in-process (no fail-closed daemon)"))
    from ..adapters.install import wiring
    for scope in ("user", "project", "local"):
        w = wiring(scope, os.getcwd())
        if not w["present"]:
            continue
        if w.get("error"):
            out.append(("fail", f"hooks ({scope})", w["error"]))
            continue
        wired = [e for e, cmds in w["events"].items() if cmds]
        if w.get("disable_all"):
            out.append(("fail", f"hooks ({scope})", "disableAllHooks is true - DinoGuard is not running"))
        elif wired:
            out.append(("ok", f"hooks ({scope})", f"{len(wired)} events wired in {w['settings']}"))
            if "PreToolUse" not in wired:
                out.append(("fail", f"hooks ({scope})", "PreToolUse is not wired"))
    if not any(c[1].startswith("hooks") for c in out):
        out.append(("warn", "hooks", "no DinoGuard hooks found; run `dinoguard install`"))
    out.extend(drift_checks())
    from ..engine.registry import build
    reg = build(os.getcwd())
    out.append(("ok", "policies", f"{len(reg.policies)} loaded"))
    for e in reg.errors:
        out.append(("warn", "policy load", f"{e['file']}: {e['error']}"))
    try:
        from ..packs.loader import verify_all
        for name, ok, detail in verify_all():
            out.append(("ok" if ok else "fail", f"pack {name}", detail))
    except ImportError:
        pass
    return out


def cmd_doctor(args) -> int:
    console.print(f"[bold]DinoGuard doctor[/] v{__version__} (protocol {PROTOCOL_VERSION})")
    worst = 0
    for status, check, detail in doctor_checks():
        icon = {"ok": OK, "warn": WARN, "fail": BAD}[status]
        console.print(f" {icon} [bold]{check}[/]: {detail}")
        worst = max(worst, {"ok": 0, "warn": 0, "fail": 1}[status])
    return worst


def cmd_status(args) -> int:
    from ..store import db
    from ..util import now_iso
    cfg = config.load()
    c = db.connect()
    today = now_iso()[:10]
    row = c.execute("SELECT COUNT(*) n, SUM(decision='allow') a, SUM(decision='instruct') i, SUM(decision='deny') d "
                    "FROM events WHERE ts >= ?", (today,)).fetchone()
    cost = c.execute("SELECT COALESCE(SUM(cost_usd),0) c, COALESCE(SUM(tokens_in+tokens_out+cache_read+cache_write),0)"
                     " t FROM model_calls WHERE ts >= ?", (today,)).fetchone()
    from ..daemon.server import is_running
    mode = "daemon" if (cfg.get("daemon") or {}).get("enabled") else "in-process"
    alive = is_running(1.0) if mode == "daemon" else None
    t = Table(show_header=False, box=None)
    t.add_row("mode", mode + ("" if alive is None else (" [green](up)[/]" if alive else " [red](DOWN - denying)[/]")))
    t.add_row("today", f"{row['n'] or 0} events · {row['a'] or 0} allowed · {row['i'] or 0} instructed · "
                       f"{row['d'] or 0} denied")
    t.add_row("tokens", f"{cost['t']:,} · ${cost['c']:.2f}")
    if cfg.get("pause_until"):
        t.add_row("pause", f"until {cfg['pause_until']}")
    ins = c.execute("SELECT COUNT(*) FROM insights WHERE status != 'resolved'").fetchone()[0]
    t.add_row("insights", f"{ins} open")
    t.add_row("environment", config.environment(cfg))
    days = int(cfg.get("retention_days") or 0)
    t.add_row("retention", f"{days} days" if days else "keep forever")
    console.print(t)
    console.print(capability_table(c))
    return 0


def drift_checks() -> list[tuple[str, str, str]]:
    """Hook drift (CLAUDE.md §17.7): native events/tools the adapters didn't recognise in the last 7 days."""
    try:
        from ..adapters import get_adapter
        from ..store import db, drift
        out = []
        for d in drift.recent(db.connect(), days=7):
            try:
                verified = get_adapter(d["harness"]).VERIFIED_VERSION
            except ValueError:
                verified = None
            out.append(("warn", f"hook drift ({d['harness']})",
                        f"unknown {d['kind']} {d['value']!r} seen {d['count']}x (harness {d['harness_version'] or '?'};"
                        f" adapter verified for {verified or 'nothing yet'}). Check docs/adapters/ and the harness "
                        "changelog."))
        return out
    except Exception as e:  # noqa: BLE001
        return [("warn", "hook drift", f"could not check: {e}")]


def capability_table(conn) -> Table:
    """What DinoGuard can block vs only observe, per harness seen or wired (CLAUDE.md §17.7)."""
    from ..adapters import get_adapter
    from ..adapters.install import wiring
    seen = {r[0] for r in conn.execute("SELECT DISTINCT harness FROM sessions WHERE harness IS NOT NULL")}
    if wiring("user", os.getcwd()).get("present"):
        seen.add("claude")
    t = Table("harness", "can block", "verified", title="capabilities (blocking after a tool ran can't undo it)")
    for h in sorted(seen):
        try:
            a = get_adapter(h)
        except ValueError:
            continue
        t.add_row(h, ", ".join(sorted(a.blocking_events)) or "nothing (observe-only)", a.VERIFIED_VERSION or "-")
    return t


def cmd_pause(args) -> int:
    if args.off:
        config.update(lambda c: c.__setitem__("pause_until", None))
        console.print(f"{OK} pause ended")
        return 0
    until = (datetime.now(timezone.utc) + timedelta(minutes=args.minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    config.update(lambda c: c.__setitem__("pause_until", until))
    console.print(f"{OK} paused until {until} (pack-unavailable denies won't fire; built-ins still run)")
    return 0


def cmd_daemon(args) -> int:
    from ..daemon import server
    if args.action == "run":
        return server.main([])
    marker = paths.home() / "daemon.stopped"       # hooks don't auto-start a daemon you stopped on purpose
    if args.action in ("stop", "restart"):
        console.print(f"{OK} stopped" if server.stop() else f"{WARN} not running")
        if args.action == "stop":
            marker.write_text("stopped by `dinoguard daemon stop`", encoding="utf-8")
            console.print(f"{WARN} guarded actions are denied until `dinoguard daemon start` (hooks won't restart it)")
            return 0
    if args.action in ("start", "restart"):
        marker.unlink(missing_ok=True)
        if server.is_running(1.0):
            console.print(f"{OK} already running")
        else:
            server.start_detached()
            console.print(f"{OK} started" if server.is_running(3.0) else f"{BAD} failed to start")
        return 0
    console.print(f"{OK} running" if server.is_running(1.0) else f"{BAD} not running")
    return 0


def maybe_first_run() -> bool:
    """Tiny wizard on first interactive `dinoguard` with no args. Skipped with DG_NO_FIRST_RUN=1 or no TTY."""
    if os.environ.get("DG_NO_FIRST_RUN") == "1" or not sys.stdin.isatty() or paths.config_path().exists():
        return False
    console.print("[bold green]Welcome to DinoGuard[/] - a guard for your coding agents.\n")
    ans = input("Install hooks for Claude Code now? [Y/n] ").strip().lower()
    if ans in ("", "y", "yes"):
        scope = input("Scope (user/project/local) [user]: ").strip() or "user"

        class A:
            harness, no_daemon = "claude", False
        A.scope = scope
        cmd_install(A)
    else:
        config.save(config.load())
        console.print("OK. Run `dinoguard install` any time.")
    return True
