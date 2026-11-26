"""Adapter interface: native hook payload <-> canonical event / native response."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..engine.decisions import Decision
from ..engine.events import CanonicalEvent

OUTPUT_LIMIT = 4000


@dataclass
class NativeResponse:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


class Adapter(ABC):
    name: str = "base"
    #: canonical events this harness can block on (documented in docs/adapters/<name>.md)
    blocking_events: frozenset[str] = frozenset()
    #: harness version / docs date the adapter was checked against (see docs/adapters/<name>.md)
    VERIFIED_VERSION: str | None = None
    #: native tool names that deliberately map to canonical "Other"; None = don't track tool drift
    known_native_tools: frozenset[str] | None = None
    #: plain-words capability notes shown by `dinoguard status`
    observe_only_note: str = "every other event is observe-only"

    @abstractmethod
    def to_canonical(self, raw: dict[str, Any]) -> CanonicalEvent: ...

    @abstractmethod
    def to_native(self, event: CanonicalEvent, decision: Decision) -> NativeResponse: ...

    def fail_closed(self, event_name: str | None, reason: str) -> NativeResponse:
        """Response used when DinoGuard itself cannot evaluate. Default: block via exit 2."""
        return NativeResponse(2, "", reason)


def response_text(resp: Any) -> tuple[str, int]:
    """Flatten a tool response into text (truncated) + original length."""
    if resp is None:
        return "", 0
    if isinstance(resp, str):
        text = resp
    elif isinstance(resp, dict):
        parts = []
        for k in ("stdout", "stderr", "output", "content", "result", "text", "error", "file"):
            v = resp.get(k)
            if isinstance(v, str) and v:
                parts.append(v)
            elif isinstance(v, dict) and isinstance(v.get("content"), str):
                parts.append(v["content"])
            elif isinstance(v, list):
                parts.extend(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in v)
        text = "\n".join(parts) if parts else json.dumps(resp, default=str)
    elif isinstance(resp, list):
        text = "\n".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in resp)
    else:
        text = str(resp)
    n = len(text)
    return (text if n <= OUTPUT_LIMIT * 4 else text[: OUTPUT_LIMIT * 4]), n
