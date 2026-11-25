"""FastAPI app for the local dashboard (localhost:8030). Plain HTML/JS, no build step."""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from .. import paths
from ..store import db

STATIC = Path(__file__).parent / "static"
_TYPES = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml"}


def _token() -> str:
    p = paths.home() / "dashboard.token"
    if not p.exists():
        p.write_text(secrets.token_hex(16), encoding="utf-8")
    return p.read_text(encoding="utf-8").strip()


def require_token(x_dinoguard_token: str | None = Header(default=None)):
    """Every mutating endpoint needs the per-install token the page is served with (blocks drive-by CSRF)."""
    if not x_dinoguard_token or not secrets.compare_digest(x_dinoguard_token, _token()):
        raise HTTPException(403, "missing or bad X-DinoGuard-Token")


def get_conn():
    c = db.connect()
    try:
        yield c
    finally:
        c.close()


def create_app() -> FastAPI:
    from . import api_core, api_ops
    app = FastAPI(title="DinoGuard", docs_url="/api/docs", redoc_url=None)
    app.include_router(api_core.router)
    app.include_router(api_ops.router)

    def page(name: str) -> HTMLResponse:
        html = (STATIC / name).read_text(encoding="utf-8").replace("__DG_TOKEN__", _token())
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/", response_class=HTMLResponse)
    def index():
        return page("index.html")

    @app.get("/runner", response_class=HTMLResponse)
    def runner():
        return page("runner.html")

    @app.get("/static/{name}")
    def static(name: str):
        f = (STATIC / name).resolve()
        if f.parent != STATIC.resolve() or not f.is_file():
            raise HTTPException(404)
        return Response(f.read_bytes(), media_type=_TYPES.get(f.suffix, "application/octet-stream"))

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.client.host if request.client else "127.0.0.1"
        if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            return Response("DinoGuard dashboard is local-only", status_code=403)
        return await call_next(request)

    return app


def serve(port: int = 8030, open_browser: bool = True) -> int:
    import threading
    import webbrowser

    import uvicorn
    url = f"http://127.0.0.1:{port}/"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"DinoGuard dashboard on {url}  (Ctrl+C to stop)")
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
    return 0


__all__ = ["create_app", "serve", "require_token", "get_conn", "Depends"]
