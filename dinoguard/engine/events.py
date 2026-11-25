"""Canonical event model. Adapters produce these; policies only ever see these."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..util import new_id, now_iso

EVENT_GROUPS: dict[str, list[str]] = {
    "tools": ["PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch"],
    "permissions": ["PermissionRequest", "PermissionDenied"],
    "prompts": ["UserPromptSubmit", "UserPromptExpansion"],
    "turn_end": ["Stop", "SubagentStop", "StopFailure"],
    "sessions": ["SessionStart", "SessionEnd", "Setup"],
    "subagents": ["SubagentStart", "TaskCreated", "TaskCompleted", "TeammateIdle"],
    "context": ["PreCompact", "PostCompact", "InstructionsLoaded"],
    "environment": ["ConfigChange", "CwdChanged", "FileChanged", "WorktreeCreate", "WorktreeRemove"],
    "interaction": ["Notification", "Elicitation", "ElicitationResult"],
}
EVENTS: list[str] = [e for group in EVENT_GROUPS.values() for e in group]

# Events where a decision can actually stop something from happening.
BLOCKING_EVENTS = frozenset({
    "PreToolUse", "PermissionRequest", "UserPromptSubmit", "UserPromptExpansion",
    "Stop", "SubagentStop", "PreCompact",
})
# Events that happen after the fact: a deny here cannot undo side effects.
OBSERVE_ONLY_NOTE = "This event happens after the action ran; blocking cannot undo side effects."

CANONICAL_TOOLS = ["Bash", "Read", "Write", "Edit", "Grep", "Glob", "WebFetch", "WebSearch", "Task", "Other"]

_TOOL_CLASS = {
    "Bash": "shell", "Write": "write", "Edit": "write", "Read": "read", "Grep": "read",
    "Glob": "read", "WebFetch": "network", "WebSearch": "network",
}


def tool_class(name: str | None) -> str:
    if not name:
        return "other"
    if name.startswith("MCP:"):
        return "other"
    return _TOOL_CLASS.get(name, "other")


def can_block(event: str) -> bool:
    return event in BLOCKING_EVENTS


@dataclass
class CanonicalEvent:
    event: str
    harness: str = "unknown"
    harness_version: str | None = None
    session: dict[str, Any] = field(default_factory=dict)
    tool: dict[str, Any] | None = None          # {"name","class","input"}
    prompt: str | None = None
    output: str | None = None                   # tool response text (post events)
    output_chars: int = 0                       # size before truncation
    error: str | None = None
    duration_ms: int | None = None
    agent_id: str | None = None
    tool_use_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)   # event-specific fields (stop_hook_active...)
    raw: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("evt"))
    ts: str = field(default_factory=now_iso)

    @property
    def session_id(self) -> str:
        return str(self.session.get("id") or "unknown")

    @property
    def tool_name(self) -> str | None:
        return self.tool.get("name") if self.tool else None

    @property
    def tool_input(self) -> dict[str, Any]:
        return (self.tool or {}).get("input") or {}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CanonicalEvent":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


def make_tool(name: str, inp: dict[str, Any] | None) -> dict[str, Any]:
    return {"name": name, "class": tool_class(name), "input": inp or {}}
