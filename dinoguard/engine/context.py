"""PolicyContext: everything a policy function may look at. Read-only by convention."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .events import CanonicalEvent


class History:
    """Read-only view of recent events in this session (oldest first)."""

    def __init__(self, items: list[dict[str, Any]] | None = None,
                 first_prompt: str | None = None, prompts: list[str] | None = None):
        self._items = items if isinstance(items, list) else list(items or [])   # no copy: read-only view
        self.first_prompt = first_prompt
        self.prompts = list(prompts or ([] if first_prompt is None else [first_prompt]))

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:   # an empty history is still a history (keeps prompts)
        return True

    def __iter__(self):
        return iter(list(self._items))

    def last(self, n: int = 10, event: str | None = None) -> list[dict[str, Any]]:
        if not n:
            return [i for i in self._items if event is None or i.get("event") == event]
        out = []
        for i in reversed(self._items):          # scan from the end; long sessions stay cheap
            if event is None or i.get("event") == event:
                out.append(i)
                if len(out) >= n:
                    break
        out.reverse()
        return out

    def tool_calls(self, n: int = 10) -> list[dict[str, Any]]:
        return self.last(n, "PreToolUse")

    @property
    def latest_prompt(self) -> str | None:
        return self.prompts[-1] if self.prompts else self.first_prompt

    def policy_fired_recently(self, policy: str, within: int = 15) -> bool:
        for item in self._items[-within:]:
            for d in item.get("decisions") or []:
                if d.get("policy") == policy and d.get("decision") in ("instruct", "deny"):
                    return True
        return False


def input_key(tool: str | None, inp: dict[str, Any] | None) -> str:
    try:
        return f"{tool}:{json.dumps(inp or {}, sort_keys=True, default=str)}"
    except (TypeError, ValueError):
        return f"{tool}:{inp!r}"


@dataclass
class TokenStats:
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float = 0.0
    context_tokens: int = 0
    context_window: int = 200_000
    burn_per_min: float = 0.0
    model: str | None = None
    turns: int = 0

    @property
    def total(self) -> int:
        return self.tokens_in + self.tokens_out + self.cache_read + self.cache_write

    @property
    def context_pct(self) -> float:
        return (self.context_tokens / self.context_window) if self.context_window else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
            "cache_read": self.cache_read, "cache_write": self.cache_write,
            "total": self.total, "cost_usd": round(self.cost_usd, 4),
            "context_tokens": self.context_tokens, "context_window": self.context_window,
            "context_pct": round(self.context_pct, 4), "burn_per_min": round(self.burn_per_min, 1),
            "model": self.model, "turns": self.turns,
        }


@dataclass
class PolicyContext:
    event: CanonicalEvent
    params: dict[str, Any] = field(default_factory=dict)
    history: History = field(default_factory=History)
    tokens: TokenStats = field(default_factory=TokenStats)
    probe: Any = None   # side-effect-free environment probe (git, gh, fs); swappable in tests
    policy_name: str = ""

    @property
    def event_type(self) -> str:
        return self.event.event

    @property
    def tool_name(self) -> str | None:
        return self.event.tool_name

    @property
    def tool_class(self) -> str:
        return (self.event.tool or {}).get("class", "other")

    @property
    def tool_input(self) -> dict[str, Any]:
        return self.event.tool_input

    @property
    def payload(self) -> dict[str, Any]:
        e = self.event
        return {"prompt": e.prompt, "output": e.output, "output_chars": e.output_chars,
                "error": e.error, **e.extra}

    @property
    def session(self) -> dict[str, Any]:
        return self.event.session

    @property
    def cwd(self) -> str:
        return str(self.event.session.get("cwd") or ".")

    @property
    def harness(self) -> str:
        return self.event.harness
