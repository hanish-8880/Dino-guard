"""`dinoguard` command line (argparse; handlers live in sibling modules and import lazily)."""
from __future__ import annotations

import argparse
import sys

from ..version import PROTOCOL_VERSION, __version__


def _p(sub, name, help_, fn):
    p = sub.add_parser(name, help=help_, description=help_)
    p.set_defaults(fn=fn)
    return p


def build_parser() -> argparse.ArgumentParser:
    from . import core, ops, pol
    ap = argparse.ArgumentParser(prog="dinoguard", description="Guardrails + observability for AI coding agents.")
    ap.add_argument("--version", action="version", version=f"dinoguard {__version__} (protocol {PROTOCOL_VERSION})")
    sub = ap.add_subparsers(dest="cmd")

    p = _p(sub, "install", "Write DinoGuard hooks into a harness and start the daemon", core.cmd_install)
    p.add_argument("--harness", default="claude")
    p.add_argument("--scope", default="user", choices=["user", "project", "local"])
    p.add_argument("--no-daemon", action="store_true", help="evaluate in the hook process (no daemon)")
    p = _p(sub, "uninstall", "Remove DinoGuard hooks and restore harness settings", core.cmd_uninstall)
    p.add_argument("--harness", default="claude")
    p.add_argument("--scope", default="user", choices=["user", "project", "local"])
    _p(sub, "doctor", "Check daemon health, version skew, hook wiring and pack integrity", core.cmd_doctor)
    _p(sub, "status", "Show guard status and today's activity", core.cmd_status)
    p = _p(sub, "pause", "Pause pack-unavailable denies for a while", core.cmd_pause)
    p.add_argument("--minutes", type=int, default=30)
    p.add_argument("--off", action="store_true", help="end the pause now")
    p = _p(sub, "daemon", "Start/stop/status the dinoguardd daemon", core.cmd_daemon)
    p.add_argument("action", choices=["start", "stop", "status", "restart", "run"])

    p = _p(sub, "runner", "Dotted dino runner (terminal strip; --web for the browser)", ops.cmd_runner)
    p.add_argument("--web", action="store_true")
    p.add_argument("--fps", type=int, default=20)
    p = _p(sub, "dashboard", "Serve the local dashboard on localhost:8030", ops.cmd_dashboard)
    p.add_argument("--port", type=int, default=8030)
    p.add_argument("--no-open", action="store_true")

    p = _p(sub, "policies", "List and manage policies and packs", pol.cmd_policies)
    p.add_argument("action", nargs="?", default="list",
                   choices=["list", "enable", "disable", "observe", "enforce", "show", "add", "remove", "set"])
    p.add_argument("target", nargs="?")
    p.add_argument("values", nargs="*", help="key=value pairs for `set`")
    p.add_argument("--policy", action="append")
    p.add_argument("--category", action="append")
    p.add_argument("--all", action="store_true")
    p.add_argument("--mode", choices=["observe", "enforce"], default="observe")
    p.add_argument("--policies", dest="policy_files", action="append", default=[])

    p = _p(sub, "policy", "Author, test, backtest, publish, deploy and roll back custom policies", pol.cmd_policy)
    p.add_argument("action", choices=["new", "test", "backtest", "publish", "deploy", "rollback", "versions"])
    p.add_argument("target", help="policy name, or plain-English text for `new`")
    p.add_argument("--name")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--version", dest="ver", type=int)
    p.add_argument("--mode", choices=["observe", "enforce"], default="observe")
    p.add_argument("--notes", default="")

    p = _p(sub, "audit", "Replay local agent history through built-in policies and report findings", ops.cmd_audit)
    p.add_argument("--schedule", type=int, metavar="DAYS")
    p.add_argument("--email")
    p.add_argument("--status", action="store_true")
    p.add_argument("--no-schedule", action="store_true")
    p.add_argument("--days", type=int, default=30, help="how far back to scan")

    p = _p(sub, "eval", "Session evaluations", ops.cmd_eval)
    p.add_argument("action", choices=["new", "test", "deploy", "backfill", "results", "list"])
    p.add_argument("name", nargs="?")
    p.add_argument("--kind", default="code", choices=["code", "judge", "classifier", "worker"])
    p.add_argument("--spec")
    p.add_argument("--condition")
    p.add_argument("--limit", type=int, default=20)

    p = _p(sub, "alerts", "Alerts: new \"tell me when ...\", test, list", ops.cmd_alerts)
    p.add_argument("action", choices=["new", "test", "list", "check", "delete"])
    p.add_argument("text", nargs="?")
    p.add_argument("--channel", action="append")
    p.add_argument("--severity", choices=["warning", "critical"], default="warning")

    p = _p(sub, "approve", "Phone approvals (setup a channel, answer, list pending)", ops.cmd_approve)
    p.add_argument("action", choices=["setup", "answer", "pending", "test"])
    p.add_argument("args", nargs="*")
    p.add_argument("--whatsapp", action="store_true")
    p.add_argument("--telegram", action="store_true")
    p.add_argument("--provider", default="twilio", choices=["twilio", "meta"])

    p = _p(sub, "channel", "Configure an alert channel (slack/email/webhook/telegram/whatsapp)", ops.cmd_channel)
    p.add_argument("name")
    p.add_argument("type", choices=["slack", "email", "webhook", "telegram", "whatsapp"])
    p.add_argument("settings", nargs="*", help="key=value")

    p = _p(sub, "reviewer", "LLM reviewer for reviewable policies", ops.cmd_reviewer)
    p.add_argument("action", choices=["setup", "status", "test"])
    p.add_argument("--url")
    p.add_argument("--model")
    p.add_argument("--mode", choices=["observe", "enforce"])
    p.add_argument("--key-stdin", action="store_true", help="read the API key from stdin")

    p = _p(sub, "ask", "Ask a question about your agent data (read-only SQL)", ops.cmd_ask)
    p.add_argument("question", nargs="+")
    _p(sub, "mcp", "Run the DinoGuard MCP server on stdio", ops.cmd_mcp)
    p = _p(sub, "publish", "Build a policy pack from local policies", ops.cmd_publish)
    p.add_argument("--name", required=False)
    p.add_argument("--publisher", default="local")
    p.add_argument("--out", default="dist-pack")
    p = _p(sub, "demo", "Scripted risky session that shows every runner state", ops.cmd_demo)
    p.add_argument("--seconds", type=int, default=60)
    p.add_argument("--no-runner", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):   # Windows consoles default to cp1252; we print ✔ and braille
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        from .core import maybe_first_run
        if maybe_first_run():
            return 0
        ap.print_help()
        return 0
    try:
        rc = args.fn(args)
    except KeyboardInterrupt:
        return 130
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
