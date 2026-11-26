"""Goose adapter. Goose now has lifecycle hooks (Open Plugins hooks spec). Verified 2026-11-26 against
goose-docs.ai/docs/guides/context-engineering/hooks and the 2026-05-14 hooks blog post. See docs/adapters/goose.md.

stdin: {"event", "session_id", "tool_name": "<extension>__<tool>", "tool_input", "working_dir", "tool_call_id",
        "message", "last_assistant_message", ...}
block (PreToolUse, Stop): stdout {"decision": "block", "reason": "..."}; allow: empty stdout.
"""
from __future__ import annotations

import json

from ..engine.decisions import DENY, Decision
from ..engine.events import CanonicalEvent, make_tool
from .base import Adapter, NativeResponse, response_text
from .common import map_generic

EVENT_MAP = {"PreToolUse": "PreToolUse", "PostToolUse": "PostToolUse", "PostToolUseFailure": "PostToolUseFailure",
             "UserPromptSubmit": "UserPromptSubmit", "Stop": "Stop", "SessionStart": "SessionStart",
             "SessionEnd": "SessionEnd", "PreToolUseResult": "Notification"}
HOOK_EVENTS = ["PreToolUse", "PostToolUse", "PostToolUseFailure", "UserPromptSubmit", "Stop", "SessionStart",
               "SessionEnd"]
_EDITOR = {"view": "Read", "write": "Write", "create": "Write", "str_replace": "Edit", "insert": "Edit"}


def map_tool(name: str | None, inp) -> tuple[str, dict]:
    args = dict(inp) if isinstance(inp, dict) else {}
    ext, sep, tool = str(name or "").partition("__")
    if not sep:
        return map_generic(name, args)
    if ext == "developer":
        if tool == "shell":
            return map_generic("Bash", args)
        if tool == "text_editor":                     # {command: view|write|str_replace|insert, path, ...}
            canon = _EDITOR.get(str(args.get("command") or ""), "Edit")
            args = {**args, "content": args.get("file_text", args.get("content", "")),
                    "old_string": args.get("old_str", ""), "new_string": args.get("new_str", args.get("file_text", ""))}
            return map_generic(canon, args)
        canon, out = map_generic(tool, args)
        if canon != "Other":
            return canon, out
    return f"MCP:{ext}:{tool or '?'}", args


class GooseAdapter(Adapter):
    name = "goose"
    VERIFIED_VERSION = "hooks docs read 2026-11-26 (version that added hooks not stated)"
    blocking_events = frozenset({"PreToolUse", "Stop"})

    def to_canonical(self, raw: dict) -> CanonicalEvent:
        native = str(raw.get("event") or raw.get("hook_event_name") or raw.get("_event") or "")
        tool = None
        if raw.get("tool_name"):
            cname, cinp = map_tool(raw.get("tool_name"), raw.get("tool_input"))
            tool = make_tool(cname, cinp)
            tool["native_name"] = raw.get("tool_name")
        output, n = (None, 0)
        if "tool_response" in raw or "tool_output" in raw:
            output, n = response_text(raw.get("tool_response", raw.get("tool_output")))
        return CanonicalEvent(event=EVENT_MAP.get(native, native or "Notification"), harness=self.name,
                              session={"id": raw.get("session_id") or "goose", "cwd": raw.get("working_dir")},
                              tool=tool, prompt=raw.get("message") if native == "UserPromptSubmit" else None,
                              output=output, output_chars=n,
                              error=(output or "tool failed")[:1000] if native == "PostToolUseFailure" else None,
                              tool_use_id=raw.get("tool_call_id"), extra={"native_event": native}, raw=raw)

    def to_native(self, event: CanonicalEvent, decision: Decision) -> NativeResponse:
        if decision.kind == DENY and event.event in self.blocking_events:
            return NativeResponse(0, json.dumps({"decision": "block",
                                                 "reason": f"[DinoGuard:{decision.policy}] {decision.reason or ''}"}), "")
        return NativeResponse(0, "", "")

    def fail_closed(self, event_name, reason):
        if event_name == "Stop":
            return NativeResponse(0, "", "")
        return NativeResponse(0, json.dumps({"decision": "block", "reason": f"[DinoGuard] {reason}"}), "")
