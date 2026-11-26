"""Price table loaded from YAML (the user's copy in the DinoGuard home wins).

An untouched copy of an older bundled table is upgraded automatically (an edited copy is never replaced), and
stored model-call costs are recalculated whenever the active table changes.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from functools import lru_cache
from pathlib import Path

import yaml

from .. import paths

BUNDLED = Path(__file__).parent / "prices.yaml"
# sha256 of earlier bundled tables (LF and CRLF). A user copy matching one was never edited, so it is upgraded.
OLD_DEFAULTS = {
    "c20a7630121699b32cc6bb103291be47c72f96737becdf0bbcad2d2e07cd947f",   # v1 (2026-11-25), LF
    "12d04b3732aa7837eb108876019bf41795d8158711b632ec42d6557f35c2af6e",   # v1, CRLF
}
_DEFAULT_ROW = {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75, "context_window": 200000}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def user_prices_path() -> Path:
    p = paths.home() / "prices.yaml"
    try:
        if not p.exists() or (_sha(p) in OLD_DEFAULTS and _sha(BUNDLED) not in OLD_DEFAULTS):
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name("prices.yaml.tmp")
            shutil.copyfile(BUNDLED, tmp)
            os.replace(tmp, p)
    except OSError:
        return p if p.exists() else BUNDLED
    return p


@lru_cache(maxsize=8)
def _load(path: str, stamp: tuple) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _stamp(p: Path) -> tuple:
    st = p.stat()
    return (st.st_mtime_ns, st.st_size)


def table() -> dict:
    p = user_prices_path()
    try:
        return _load(str(p), _stamp(p))
    except (OSError, yaml.YAMLError):
        return _load(str(BUNDLED), _stamp(BUNDLED))


def for_model(model: str | None) -> dict:
    t = table()
    m = (model or "").lower()
    for row in t.get("models") or []:
        if str(row.get("match", "")).lower() in m and row.get("match"):
            return {**(t.get("default") or {}), **row}
    return dict(t.get("default") or _DEFAULT_ROW)


def cost(model: str | None, tokens_in: int, tokens_out: int, cache_read: int = 0, cache_write: int = 0) -> float:
    p = for_model(model)
    return (tokens_in * p.get("input", 0) + tokens_out * p.get("output", 0) + cache_read * p.get("cache_read", 0)
            + cache_write * p.get("cache_write", 0)) / 1_000_000


def signature() -> str:
    """Fingerprint of the active price table (changes whenever the user edits it)."""
    p = user_prices_path()
    try:
        return _sha(p)
    except OSError:
        return _sha(BUNDLED)


def _applied_path() -> Path:
    return paths.home() / "prices.applied"


def ensure_repriced(conn: sqlite3.Connection) -> int:
    """Recalculate stored model-call costs if the price table changed since they were computed."""
    sig = signature()
    marker = _applied_path()
    try:
        if marker.read_text(encoding="utf-8").strip() == sig:
            return 0
    except OSError:
        pass
    rows = conn.execute("SELECT id, model, tokens_in, tokens_out, cache_read, cache_write FROM model_calls").fetchall()
    for r in rows:
        c = cost(r[1], r[2] or 0, r[3] or 0, r[4] or 0, r[5] or 0)
        conn.execute("UPDATE model_calls SET cost_usd=? WHERE id=?", (c, r[0]))
    conn.commit()
    try:
        marker.write_text(sig, encoding="utf-8")
    except OSError:
        pass
    return len(rows)
