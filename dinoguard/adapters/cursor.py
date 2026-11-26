"""Cursor agent hooks adapter. Verified against https://cursor.com/docs/agent/hooks (see docs/adapters/cursor.md)."""
from __future__ import annotations

import json

from ..engine.decisions import DENY, INSTRUCT
from ..engine.events import CanonicalEvent, make_tool
from .base import Adapter, NativeResponse, response_text
from .common import map_generic, parse_args

EVENT_MAP = {
    "preToolUse": "PreToolUse", "postToolUse": "PostToolUse", "postToolUseFailure": "PostToolUseFailure",
    "beforeShellExecution": "PreToolUse", "afterShellExecution": "PostToolUse",
    "beforeMCPExecution": "PreToolUse", "afterMCPExecution": "PostToolUse",
    "beforeReadFile": "PreToolUse", "afterFileEdit": "PostToolUse", "beforeSubmitPrompt": "UserPromptSubmit",
    "stop": "Stop", "subagentStop": "SubagentStop", "subagentStart": "SubagentStart", "sessionStart": "SessionStart",
    "sessionEnd": "SessionEnd", "preCompact": "PreCompact", "afterAgentResponse": "Notification",
    "afterAgentThought": "Notification",
}
PERMISSION_EVENTS = {"preToolUse", "beforeShellExecution", "beforeMCPExecution", "beforeReadFile"}


class CursorAdapter(Adapter):
    name = "cursor"
    VERIFIED_VERSION = "docs read 2026-11-25 (cursor.com/docs/agent/hooks)"
    blocking_events = frozenset({"PreToolUse", "UserPromptSubmit", "Stop", "SubagentStop"})

    def _tool(self, ev: str, raw: dict):
        if ev in ("beforeShellExecution", "afterShellExecution"):
            return make_tool("Bash", {"command": str(raw.get("command", "")), "sandbox": raw.get("sandbox")})
        if ev in ("beforeMCPExecution", "afterMCPExecution"):
            server = raw.get("mcp_server_name") or "?"
            return make_tool(f"MCP:{server}:{raw.get('tool_name', '?')}", parse_args(raw.get("tool_input")))
        if ev == "beforeReadFile":
            return make_tool("Read", {"file_path": str(raw.get("file_path", ""))})
        if ev == "afterFileEdit":
            edits = raw.get("edits") or []
            return make_tool("Edit", {"file_path": str(raw.get("file_path", "")),
                                      "old_string": "\n".join(str(e.get("old_string", "")) for e in edits),
                                      "new_string": "\n".join(str(e.get("new_string", "")) for e in edits)})
        if raw.get("tool_name"):
            n = str(raw["tool_name"])
            if n.startswith("MCP:"):
                return make_tool(f"MCP:cursor:{n[4:]}", parse_args(raw.get("tool_input")))
            if n == "Delete":
                return make_tool("Other", {"_tool": "Delete", **parse_args(raw.get("tool_input"))})
            name, inp = map_generic(n, raw.get("tool_input"))
            return make_tool(name, inp)
        return None

    def to_canonical(self, raw: dict) -> CanonicalEvent:
        native = str(raw.get("hook_event_name") or "")
        ev = EVENT_MAP.get(native, native or "Notification")
        roots = raw.get("workspace_roots") or []
        out, n = response_text(raw.get("output") or raw.get("tool_output") or raw.get("result"))
        return CanonicalEvent(
            event=ev, harness="cursor", harness_version=raw.get("cursor_version"),
            session={"id": raw.get("conversation_id") or "unknown", "cwd": raw.get("cwd") or (roots[0] if roots else None),
                     "transcript_path": raw.get("transcript_path"), "generation_id": raw.get("generation_id")},
            tool=self._tool(native, raw), prompt=raw.get("prompt"), output=out or None, output_chars=n,
            error=str(raw.get("error")) if ev == "PostToolUseFailure" else None,
            extra={"native_event": native, "loop_count": raw.get("loop_count"), "status": raw.get("status")}, raw=raw)

    def to_native(self, event: CanonicalEvent, decision) -> NativeResponse:
        native = event.extra.get("native_event", "")
        reason = f"[DinoGuard:{decision.policy}] {decision.reason or ''}"
        if decision.kind == DENY:
            if native in PERMISSION_EVENTS:
                return NativeResponse(0, json.dumps({"continue": True, "permission": "deny", "user_message": reason,
                                                     "agent_message": reason}), "")
            if native == "beforeSubmitPrompt":
                return NativeResponse(0, json.dumps({"continue": False, "user_message": reason}), "")
            if native in ("stop", "subagentStop"):
                return NativeResponse(0, json.dumps({"followup_message": reason}), "")
            if native in ("postToolUse", "postToolUseFailure"):
                return NativeResponse(0, json.dumps({"additional_context": reason}), "")
            return NativeResponse(0, "", "")
        if decision.kind == INSTRUCT:
            if native in ("postToolUse", "postToolUseFailure", "sessionStart"):
                return NativeResponse(0, json.dumps({"additional_context": f"[DinoGuard] {decision.reason}"}), "")
            if native in PERMISSION_EVENTS:
                # no documented context channel before a tool runs: pass the note to the agent, keep allowing
                return NativeResponse(0, json.dumps({"agent_message": f"[DinoGuard] {decision.reason}"}), "")
        return NativeResponse(0, "", "")

    def fail_closed(self, event_name, reason):
        reason = f"[DinoGuard] {reason}"
        if event_name in PERMISSION_EVENTS or event_name is None:
            return NativeResponse(2, json.dumps({"continue": True, "permission": "deny", "user_message": reason,
                                                 "agent_message": reason}), reason)
        return NativeResponse(0, "", "")
