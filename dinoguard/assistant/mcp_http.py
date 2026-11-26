"""`dinoguard mcp --http`: the same MCP server over local HTTP (CLAUDE.md §17.11).

POST /mcp with one JSON-RPC message (or a batch). Binds 127.0.0.1 only and needs the per-install token as
`Authorization: Bearer <token>` or `X-DinoGuard-Token`. Responses are plain JSON (no SSE streams).

    claude mcp add --transport http dinoguard http://127.0.0.1:8031/mcp --header "Authorization: Bearer <token>"
"""
from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .mcp_server import handle

MAX_BODY = 1024 * 1024


def make_server(port: int = 8031, token: str | None = None, conn_factory=None) -> ThreadingHTTPServer:
    from ..dashboard.app import _token
    from ..store.db import connect
    token = token or _token()
    conn_factory = conn_factory or connect
    lock = threading.Lock()
    state: dict = {}

    def conn():
        if "c" not in state:
            state["c"] = conn_factory()
        return state["c"]

    class H(BaseHTTPRequestHandler):
        server_version = "dinoguard-mcp"

        def log_message(self, *a):          # no request logging: arguments may be sensitive
            pass

        def _send(self, code: int, body: dict | list | None):
            data = b"" if body is None else json.dumps(body, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            got = self.headers.get("X-DinoGuard-Token") or ""
            auth = self.headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                got = got or auth[7:].strip()
            return bool(got) and secrets.compare_digest(got, token)

        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/") == "/health":
                return self._send(200, {"ok": True})
            self._send(405, {"error": "use POST /mcp"})

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                self.close_connection = True
                return self._send(413, {"error": "body too large"})
            raw = self.rfile.read(n) if n else b""     # read first: replying early resets the socket on Windows
            if self.path.split("?")[0].rstrip("/") != "/mcp":
                return self._send(404, {"error": "not found"})
            if not self._authorized():
                return self._send(401, {"error": "missing or bad token"})
            try:
                msg = json.loads(raw or b"null")
            except ValueError:
                return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            with lock:
                if isinstance(msg, list):
                    out = [r for r in (handle(conn(), m, "http") for m in msg if isinstance(m, dict)) if r]
                    return self._send(200, out) if out else self._send(202, None)
                if not isinstance(msg, dict):
                    return self._send(400, {"error": "expected a JSON-RPC object"})
                resp = handle(conn(), msg, "http")
            return self._send(200, resp) if resp else self._send(202, None)

    return ThreadingHTTPServer(("127.0.0.1", port), H)


def serve_http(port: int = 8031) -> int:
    srv = make_server(port)
    print(f"DinoGuard MCP on http://127.0.0.1:{srv.server_address[1]}/mcp (token: dashboard.token in the "
          "DinoGuard home). Ctrl+C to stop.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
