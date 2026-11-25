"""Adapter lookup by harness name."""
from __future__ import annotations

import importlib

from .base import Adapter

_ADAPTERS = {
    "claude": ("claude_code", "ClaudeCodeAdapter"),
    "claude-code": ("claude_code", "ClaudeCodeAdapter"),
    "cursor": ("cursor", "CursorAdapter"),
    "codex": ("codex", "CodexAdapter"),
    "copilot": ("copilot", "CopilotAdapter"),
    "gemini": ("gemini", "GeminiAdapter"),
    "opencode": ("opencode", "OpenCodeAdapter"),
    "goose": ("goose", "GooseAdapter"),
    "sdk": ("sdk", "SDKAdapter"),
}


def names() -> list[str]:
    return sorted(k for k in _ADAPTERS if k != "claude-code")


def get_adapter(name: str) -> Adapter:
    try:
        mod_name, cls_name = _ADAPTERS[name.lower()]
    except KeyError:
        raise ValueError(f"unknown harness {name!r}; known: {', '.join(names())}") from None
    mod = importlib.import_module(f"{__name__}.{mod_name}")
    return getattr(mod, cls_name)()
