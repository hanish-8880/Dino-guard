"""Local transport: AF_UNIX socket where available, else 127.0.0.1 TCP + port file with an auth token."""
from __future__ import annotations

import json
import os
import socket

from .. import paths

HAS_UNIX = hasattr(socket, "AF_UNIX") and os.name != "nt"
MAX_MSG = 8 * 1024 * 1024


class TransportError(OSError):
    pass


def read_endpoint() -> dict:
    if HAS_UNIX:
        p = paths.socket_path()
        if not p.exists():
            raise TransportError(f"socket {p} not found (daemon not running)")
        return {"kind": "unix", "path": str(p), "token": None}
    pf = paths.port_file()
    if not pf.exists():
        raise TransportError(f"port file {pf} not found (daemon not running)")
    try:
        data = json.loads(pf.read_text(encoding="utf-8"))
        return {"kind": "tcp", "host": "127.0.0.1", "port": int(data["port"]), "token": data.get("token")}
    except (ValueError, KeyError) as e:
        raise TransportError(f"port file unreadable: {e}") from e


def connect(timeout: float) -> tuple[socket.socket, str | None]:
    ep = read_endpoint()
    if ep["kind"] == "unix":
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(ep["path"])
    else:
        s = socket.create_connection((ep["host"], ep["port"]), timeout=timeout)
        s.settimeout(timeout)
    return s, ep["token"]


def send_msg(sock: socket.socket, obj: dict) -> None:
    sock.sendall(json.dumps(obj, default=str).encode("utf-8") + b"\n")


def recv_msg(sock_file) -> dict:
    line = sock_file.readline(MAX_MSG)
    if not line:
        raise TransportError("connection closed")
    return json.loads(line.decode("utf-8"))
