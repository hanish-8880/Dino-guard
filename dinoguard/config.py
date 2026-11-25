"""User configuration in ~/.dinoguard/config.json (plain JSON, atomic writes)."""
from __future__ import annotations

import copy
import json
import os
import tempfile
from typing import Any

from . import paths

DEFAULTS: dict[str, Any] = {
    "daemon": {"enabled": False},
    "policies": {},          # name -> {"enabled": bool, "mode": "observe"|"enforce", "params": {...}}
    "pause_until": None,     # ISO ts; session pause
    "reviewer": {"url": None, "model": "gpt-4o-mini", "mode": "observe", "timeout_s": 8},
    "approvals": {"channel": None, "timeout_s": 120},
    "channels": {},          # name -> channel config (telegram/whatsapp/slack/email/webhook)
    "runner": {"burn_high_per_min": 20000},
    "llm": {"url": None, "model": "gpt-4o-mini"},   # used by authoring / assistant / judge
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict[str, Any]:
    p = paths.config_path()
    data: dict[str, Any] = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            data = {}
    return _merge(copy.deepcopy(DEFAULTS), data)


def save(cfg: dict[str, Any]) -> None:
    p = paths.config_path()
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".cfg-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
    os.replace(tmp, p)


def update(fn) -> dict[str, Any]:
    cfg = load()
    fn(cfg)
    save(cfg)
    return cfg


def policy_override(cfg: dict, name: str) -> dict:
    return cfg.get("policies", {}).get(name, {})


def set_policy(name: str, **fields) -> dict:
    def _f(cfg):
        cur = cfg.setdefault("policies", {}).setdefault(name, {})
        for k, v in fields.items():
            if k == "params":
                cur.setdefault("params", {}).update(v)
            else:
                cur[k] = v
    return update(_f)


def load_secrets() -> dict[str, Any]:
    p = paths.secrets_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_secret(key: str, value: str) -> None:
    data = load_secrets()
    data[key] = value
    p = paths.secrets_path()
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def secret(key: str, env: str | None = None) -> str | None:
    if env and os.environ.get(env):
        return os.environ[env]
    return load_secrets().get(key)
