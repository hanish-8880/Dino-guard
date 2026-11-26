"""dinoguardd: the single evaluator once configured. Newline-delimited JSON over a local socket.

Ops: health, evaluate, reload, stop. Every response carries the protocol version; the client
denies on mismatch (fail closed).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import socket
import socketserver
import sys
import threading
import time

from .. import paths
from ..engine.events import CanonicalEvent
from ..util import now_iso
from ..version import PROTOCOL_VERSION, __version__
from . import transport

TICK_S = 30.0


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        srv: "Daemon" = self.server.daemon  # type: ignore[attr-defined]
        try:
            req = transport.recv_msg(self.rfile)
        except (ValueError, OSError, transport.TransportError):
            return
        resp = srv.dispatch(req)
        resp.setdefault("v", PROTOCOL_VERSION)
        resp.setdefault("server", __version__)
        try:
            self.wfile.write(json.dumps(resp, default=str).encode("utf-8") + b"\n")
        except OSError:
            pass


class _TCP(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


if transport.HAS_UNIX:
    class _UNIX(socketserver.ThreadingUnixStreamServer):  # type: ignore[name-defined]
        daemon_threads = True


class Daemon:
    def __init__(self, service=None):
        if service is None:
            from ..service import Service
            service = Service()
        self.service = service
        self.started_at = now_iso()
        self.token = secrets.token_hex(16)
        self.requests = 0
        self.server = None
        self._stop = threading.Event()

    # --- protocol -------------------------------------------------------
    def dispatch(self, req: dict) -> dict:
        self.requests += 1
        if req.get("v") != PROTOCOL_VERSION:
            return {"ok": False, "error": f"protocol mismatch (client v{req.get('v')}, daemon v{PROTOCOL_VERSION})"}
        if not transport.HAS_UNIX and req.get("token") != self.token:
            return {"ok": False, "error": "bad token"}
        op = req.get("op")
        try:
            if op == "health":
                reg = self.service.registry_for(None)
                return {"ok": True, "pid": os.getpid(), "started_at": self.started_at, "requests": self.requests,
                        "policies": len(reg.policies), "load_errors": reg.errors, "version": __version__}
            if op == "evaluate":
                ev = CanonicalEvent.from_dict(req["event"])
                result = self.service.handle(ev)
                return {"ok": True, "decision": result.decision.to_dict()}
            if op == "reload":
                self.service.reload()
                return {"ok": True}
            if op == "stop":
                threading.Thread(target=self.shutdown, daemon=True).start()
                return {"ok": True}
        except Exception as e:  # noqa: BLE001 - reported to the client, which fails closed
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return {"ok": False, "error": f"unknown op {op!r}"}

    # --- lifecycle ------------------------------------------------------
    def bind(self):
        if transport.HAS_UNIX:
            sp = paths.socket_path()
            if sp.exists():
                sp.unlink()
            self.server = _UNIX(str(sp), _Handler)
            os.chmod(sp, 0o600)
        else:
            self.server = _TCP(("127.0.0.1", 0), _Handler)
            port = self.server.server_address[1]
            pf = paths.port_file()
            pf.write_text(json.dumps({"port": port, "token": self.token, "pid": os.getpid()}), encoding="utf-8")
        self.server.daemon = self  # type: ignore[attr-defined]
        paths.pid_file().write_text(str(os.getpid()), encoding="utf-8")
        return self

    def _ticker(self):
        while not self._stop.wait(TICK_S):
            tick(self.service)

    def serve(self):
        threading.Thread(target=self._ticker, daemon=True, name="dg-ticker").start()
        try:
            self.server.serve_forever(poll_interval=0.2)
        finally:
            self.cleanup()

    def serve_in_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        t.start()
        return t

    def shutdown(self):
        self._stop.set()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.cleanup()

    def cleanup(self):
        for p in (paths.port_file(), paths.socket_path(), paths.pid_file()):
            try:
                if p.exists() and (p != paths.pid_file() or p.read_text().strip() == str(os.getpid())):
                    p.unlink()
            except OSError:
                pass


def tick(service) -> None:
    """Background work: alert checks and scheduled audits. Never raises."""
    for mod, fn in (("..alerts.engine", "check_due"), ("..audit.schedule", "run_due"),
                    ("..store.retention", "run_due")):
        try:
            import importlib
            m = importlib.import_module(mod, __package__)
            getattr(m, fn)(service.conn, service.cfg)
        except Exception:  # noqa: BLE001
            pass


def is_running(timeout: float = 2.0) -> bool:
    from .client import DaemonError, health
    try:
        health(timeout)
        return True
    except DaemonError:
        return False


def start_detached() -> int:
    """Spawn the daemon in the background; returns the child pid."""
    import subprocess
    args = [sys.executable, "-m", "dinoguard.daemon.server"]
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "close_fds": True, "env": os.environ.copy()}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen(args, **kw)
    for _ in range(50):
        if is_running(0.5):
            break
        time.sleep(0.1)
    return p.pid


def stop() -> bool:
    from .client import DaemonError, request
    try:
        request("stop", timeout=3.0)
        return True
    except DaemonError:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dinoguardd", description="DinoGuard evaluator daemon")
    ap.add_argument("--version", action="version", version=f"dinoguardd {__version__} (protocol {PROTOCOL_VERSION})")
    ap.add_argument("--login", action="store_true", help="started on login: skip if the user stopped it on purpose")
    args = ap.parse_args(argv)
    if args.login and (paths.home() / "daemon.stopped").exists():
        return 0
    if is_running(1.0):
        if sys.stderr:                       # None under pythonw (the login shortcut has no console)
            print("dinoguardd already running", file=sys.stderr)
        return 0
    d = Daemon().bind()
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=d.shutdown, daemon=True).start())
    try:
        d.serve()
    except KeyboardInterrupt:
        d.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
