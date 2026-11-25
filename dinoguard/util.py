"""Small shared helpers: time, ids, path normalisation, json."""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_iso(ts: str) -> datetime:
    ts = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def new_id(prefix: str = "") -> str:
    u = uuid.uuid4().hex
    return f"{prefix}_{u}" if prefix else u


def norm_path(p: str | None, cwd: str | None = None) -> str:
    """Normalise a path for comparisons: forward slashes, ~ expanded, absolute if cwd given."""
    if not p:
        return ""
    p = os.path.expanduser(str(p))
    if cwd and not os.path.isabs(p):
        p = os.path.join(cwd, p)
    if cwd or os.path.isabs(p):
        p = os.path.normpath(p)
    p = p.replace("\\", "/")
    return p


def path_key(p: str, cwd: str | None = None) -> str:
    """Comparison key: normalised and case-folded on case-insensitive platforms."""
    n = norm_path(p, cwd)
    return n.lower() if sys.platform.startswith("win") or sys.platform == "darwin" else n


def is_within(child: str, parent: str) -> bool:
    c, p = path_key(child), path_key(parent).rstrip("/")
    return c == p or c.startswith(p + "/")


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def truncate(text: str | None, n: int = 2000) -> str | None:
    if text is None:
        return None
    return text if len(text) <= n else text[:n] + f"... [{len(text) - n} more chars]"
