"""Effective per-policy settings: enabled, observe/enforce, params (defaults + overrides)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OBSERVE, ENFORCE = "observe", "enforce"


@dataclass
class Effective:
    enabled: bool
    mode: str
    params: dict[str, Any]


def effective(policy, cfg: dict) -> Effective:
    over = (cfg.get("policies") or {}).get(policy.name, {})
    params = dict(policy.params or {})
    params.update(over.get("params") or {})
    if policy.always_on:
        return Effective(True, ENFORCE, params)
    enabled = bool(over.get("enabled", policy.default_enabled))
    mode = over.get("mode", policy.default_mode)
    if mode not in (OBSERVE, ENFORCE):
        mode = OBSERVE
    return Effective(enabled, mode, params)
