"""Hook-side daemon client. Any problem raises DaemonError; the hook turns that into a deny."""
from __future__ import annotations

import socket

from ..engine.decisions import Decision
from ..version import PROTOCOL_VERSION, __version__
from . import transport

DEFAULT_TIMEOUT = 30.0


class DaemonError(RuntimeError):
    pass


def timeout_for(cfg: dict) -> float:
    appr = int((cfg.get("approvals") or {}).get("timeout_s", 120))
    return max(DEFAULT_TIMEOUT, appr + 15.0) if (cfg.get("approvals") or {}).get("channel") else DEFAULT_TIMEOUT


def request(op: str, payload: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    try:
        sock, token = transport.connect(min(timeout, 3.0))
    except (OSError, transport.TransportError) as e:
        raise DaemonError(f"daemon unreachable ({e})") from e
    try:
        sock.settimeout(timeout)
        transport.send_msg(sock, {"v": PROTOCOL_VERSION, "client": __version__, "token": token, "op": op,
                                  **(payload or {})})
        with sock.makefile("rb") as f:
            resp = transport.recv_msg(f)
    except socket.timeout as e:
        raise DaemonError(f"daemon timed out after {timeout:.0f}s") from e
    except (OSError, ValueError, transport.TransportError) as e:
        raise DaemonError(f"daemon connection failed ({e})") from e
    finally:
        try:
            sock.close()
        except OSError:
            pass
    if resp.get("v") != PROTOCOL_VERSION:
        raise DaemonError(f"protocol version mismatch: hook speaks v{PROTOCOL_VERSION}, daemon speaks "
                          f"v{resp.get('v')} (daemon {resp.get('server')}, hook {__version__})")
    if not resp.get("ok"):
        raise DaemonError(f"daemon error: {resp.get('error', 'unknown')}")
    return resp


def evaluate(event, timeout: float = DEFAULT_TIMEOUT) -> Decision:
    resp = request("evaluate", {"event": event.to_dict()}, timeout)
    try:
        return Decision.from_dict(resp["decision"])
    except (KeyError, TypeError, ValueError) as e:
        raise DaemonError(f"malformed daemon response ({e})") from e


def health(timeout: float = 3.0) -> dict:
    return request("health", timeout=timeout)
