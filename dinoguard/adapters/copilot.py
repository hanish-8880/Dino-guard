"""GitHub Copilot (CLI / cloud agent) hooks adapter.
Verified against https://docs.github.com/en/copilot/reference/hooks-reference (docs/adapters/copilot.md)."""
from __future__ import annotations

import json

from ..engine.decisions import DENY, INSTRUCT
from ..engine.events import CanonicalEvent, make_tool
from .base import Adapter, NativeResponse, response_text
from .common import map_generic

EVENT_MAP = {"preToolUse": "PreToolUse", "postToolUse": "PostToolUse", "postToolUseFailure": "PostToolUseFailure",
             "userPromptSubmitted": "UserPromptSubmit", "sessionStart": "SessionStart", "sessionEnd": "SessionEnd",
             "agentStop": "Stop", "subagentStart": "SubagentStart", "subagentStop": "SubagentStop",
             "preCompact": "PreCompact", "permissionRequest": "PermissionRequest", "errorOccurred": "StopFailure",
             "notification": "Notification", "userPromptTransformed": "UserPromptExpansion"}


class CopilotAdapter(Adapter):
    name = "copilot"
    VERIFIED_VERSION = "docs read 2026-11-25 (GitHub Copilot hooks reference)"
    blocking_events = frozenset({"PreToolUse"})

    def to_canonical(self, raw: dict) -> CanonicalEvent:
        # camelCase payload (Copilot format) or snake_case (VS Code / PascalCase hook names)
        native = str(raw.get("hookEventName") or raw.get("hook_event_name") or raw.get("_event") or "")
        ev = EVENT_MAP.get(native[:1].lower() + native[1:], native or "Notification")
        tname = raw.get("toolName") or raw.get("tool_name")
        tool = None
        if tname:
            name, inp = map_generic(tname, raw.get("toolArgs") if "toolArgs" in raw else raw.get("tool_input"))
            tool = make_tool(name, inp)
        res = raw.get("toolResult") or raw.get("tool_response")
        out, n = response_text(res.get("textResultForLlm") if isinstance(res, dict) else res)
        failed = isinstance(res, dict) and res.get("resultType") in ("failure", "error", "denied")
        return CanonicalEvent(
            event="PostToolUseFailure" if ev == "PostToolUse" and failed else ev, harness="copilot",
            session={"id": raw.get("sessionId") or raw.get("session_id") or "unknown", "cwd": raw.get("cwd")},
            tool=tool, prompt=raw.get("prompt"), output=out or None, output_chars=n,
            error="tool failed" if failed else None, extra={"native_event": native}, raw=raw)

    def to_native(self, event, decision) -> NativeResponse:
        if decision.kind == DENY and event.event in ("PreToolUse", "PermissionRequest"):
            return NativeResponse(0, json.dumps({"permissionDecision": "deny",
                                                 "permissionDecisionReason": f"[DinoGuard:{decision.policy}] {decision.reason}"}), "")
        if decision.kind in (DENY, INSTRUCT) and event.event in ("PostToolUse", "PostToolUseFailure", "SubagentStart"):
            return NativeResponse(0, json.dumps({"additionalContext": f"[DinoGuard] {decision.reason}"}), "")
        return NativeResponse(0, "", "")

    def fail_closed(self, event_name, reason):
        return NativeResponse(2, json.dumps({"permissionDecision": "deny",
                                             "permissionDecisionReason": f"[DinoGuard] {reason}"}), reason)
