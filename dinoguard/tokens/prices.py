"""Price table loaded from YAML (user copy in ~/.dinoguard/prices.yaml wins)."""
from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

import yaml

from .. import paths

BUNDLED = Path(__file__).parent / "prices.yaml"


def user_prices_path() -> Path:
    p = paths.home() / "prices.yaml"
    if not p.exists():
        try:
            shutil.copyfile(BUNDLED, p)
        except OSError:
            return BUNDLED
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
    return dict(t.get("default") or {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75,
                                      "context_window": 200000})


def cost(model: str | None, tokens_in: int, tokens_out: int, cache_read: int = 0, cache_write: int = 0) -> float:
    p = for_model(model)
    return (tokens_in * p.get("input", 0) + tokens_out * p.get("output", 0) + cache_read * p.get("cache_read", 0)
            + cache_write * p.get("cache_write", 0)) / 1_000_000
