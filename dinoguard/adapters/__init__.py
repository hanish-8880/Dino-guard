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
    "devin": ("claude_like", "DevinAdapter"),
    "droid": ("claude_like", "DroidAdapter"),
    "deepagents": ("claude_like", "DeepAgentsCodeAdapter"),
    "dcode": ("claude_like", "DeepAgentsCodeAdapter"),
    "hermes": ("hermes", "HermesAdapter"),
    "antigravity": ("antigravity", "AntigravityAdapter"),
    "agy": ("antigravity", "AntigravityAdapter"),
    "pi": ("plugin_bridge", "PiAdapter"),
    "openclaw": ("plugin_bridge", "OpenClawAdapter"),
}
_ALIASES_HIDDEN = {"claude-code", "dcode", "agy"}


def names() -> list[str]:
    return sorted(k for k in _ADAPTERS if k not in _ALIASES_HIDDEN)


def sniff(harness: str) -> str:
    """Harnesses that also read Claude Code's settings run our `claude` hook; tell them apart by the
    environment they set for hooks (Devin: DEVIN_PROJECT_DIR, Factory Droid: FACTORY_PROJECT_DIR)."""
    import os
    if harness.lower() in ("claude", "claude-code"):
        if os.environ.get("DEVIN_PROJECT_DIR"):
            return "devin"
        if os.environ.get("FACTORY_PROJECT_DIR") or os.environ.get("DROID_PROJECT_DIR"):
            return "droid"
    return harness


def get_adapter(name: str) -> Adapter:
    try:
        mod_name, cls_name = _ADAPTERS[name.lower()]
    except KeyError:
        raise ValueError(f"unknown harness {name!r}; known: {', '.join(names())}") from None
    mod = importlib.import_module(f"{__name__}.{mod_name}")
    return getattr(mod, cls_name)()
