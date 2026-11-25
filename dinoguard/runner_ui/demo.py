"""`dinoguard demo`: a scripted risky session that shows every runner state in ~60 s.

Runs in a throwaway DINOGUARD_HOME so it never touches your real data. Decisions come from the
real engine; review / approval / guard-down are shown with stream notes.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time

SCRIPT = [
    ("bash", "npm test", "walk: allowed"),
    ("read", "src/app.ts", "walk"),
    ("bash", 'psql -c "DROP TABLE sessions"', "instruct: destructive SQL"),
    ("bash", "rm -rf src", "deny: recursive force delete"),
    ("note", "review", "review: reviewer scanning"),
    ("burn", None, "sprint: token burn high"),
    ("bash", "git push --force", "deny: force push"),
    ("bash", "dinoguard uninstall", "shield: self-protection"),
    ("bash", "chmod 777 deploy.sh", "ghost: observe-mode would deny"),
    ("note", "approval_wait", "wait: phone approval"),
    ("note", "approval_done", "approved"),
    ("note", "guard_down", "guard down: daemon unreachable"),
    ("bash", "ls", "walk: back to normal"),
]


def _feed(seconds: int, stop: threading.Event):
    from .. import config
    from ..engine.events import CanonicalEvent, make_tool
    from ..service import Service
    from ..store.writer import stream_note
    config.set_policy("no-chmod-777", enabled=True, mode="observe")
    svc = Service()
    step = max(2.0, seconds / len(SCRIPT))
    burn = {"context_tokens": 60000, "context_window": 200000, "burn_per_min": 500, "cost_usd": 0.12}
    for kind, arg, _label in SCRIPT:
        if stop.is_set():
            return
        if kind in ("bash", "read"):
            tool = make_tool("Bash", {"command": arg}) if kind == "bash" else make_tool("Read", {"file_path": arg})
            svc.handle(CanonicalEvent(event="PreToolUse", harness="demo", session={"id": "demo", "cwd": os.getcwd()},
                                      tool=tool))
        elif kind == "burn":
            burn = {**burn, "burn_per_min": 45000, "context_tokens": 150000, "cost_usd": 1.84}
            stream_note("event", session="demo", event="PreToolUse", tool="Read", summary="huge.log",
                        decision="instruct", policy="token-big-output",
                        reason="that output was ~48k tokens; grep for what you need", tokens=burn)
        elif arg == "review":
            stream_note("review", session="demo", summary="kubectl apply -f prod.yaml")
        elif arg == "approval_wait":
            stream_note("approval_wait", session="demo", code="4821", summary="terraform apply", timeout_s=120)
        elif arg == "approval_done":
            stream_note("approval_done", session="demo", answer="yes", code="4821")
        elif arg == "guard_down":
            stream_note("guard_down", session="demo", reason="daemon unreachable - every action denied")
        time.sleep(step)
    stop.set()


def run_demo(seconds: int = 60, show_runner: bool = True) -> int:
    home = tempfile.mkdtemp(prefix="dinoguard-demo-")
    os.environ["DINOGUARD_HOME"] = home
    stop = threading.Event()
    t = threading.Thread(target=_feed, args=(seconds, stop), daemon=True)
    t.start()
    if show_runner:
        from .app import run
        run(fps=20, max_seconds=seconds + 3)
    else:
        t.join()
    stop.set()
    print(f"demo data left in {home}")
    return 0
