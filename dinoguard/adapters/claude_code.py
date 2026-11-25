"""Claude Code adapter. Verified against https://code.claude.com/docs/en/hooks (see docs/adapters/claude-code.md)."""
from __future__ import annotations

import json
from typing import Any

from ..engine.decisions import DENY, INSTRUCT, Decision
from ..engine.events import EVENTS, CanonicalEvent, make_tool
from .base import Adapter, NativeResponse, response_text

_COMMON = {"session_id", "transcript_path", "cwd", "permission_mode", "hook_event_name", "tool_name",
           "tool_input", "tool_response", "tool_use_id", "prompt", "agent_id", "agent_type", "prompt_id",
           "scratchpad_dir", "effort", "duration_ms", "mcp_server"}


def map_tool(name: str | None, inp: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """Claude Code tool name + input -> canonical tool name + canonical input fields."""
    inp = dict(inp or {})
    if not name:
        return "Other", inp
    if name.startswith("mcp__"):
        parts = name.split("__")
        server = parts[1] if len(parts) > 1 else "?"
        tool = "__".join(parts[2:]) if len(parts) > 2 else "?"
        return f"MCP:{server}:{tool}", inp
    if name in ("Bash", "PowerShell"):
        out = {"command": str(inp.get("command", ""))}
        if name == "PowerShell":
            out["shell"] = "powershell"
        for k in ("description", "timeout", "run_in_background"):
            if k in inp:
                out[k] = inp[k]
        return "Bash", out
    if name == "MultiEdit":
        edits = inp.get("edits") or []
        return "Edit", {"file_path": inp.get("file_path", ""),
                        "old_string": "\n".join(str(e.get("old_string", "")) for e in edits),
                        "new_string": "\n".join(str(e.get("new_string", "")) for e in edits),
                        "edits": edits}
    if name == "NotebookEdit":
        return "Edit", {"file_path": inp.get("notebook_path", ""), "old_string": "",
                        "new_string": str(inp.get("new_source", "")), **inp}
    if name in ("Agent", "Task"):
        return "Task", inp
    if name in ("Read", "Write", "Edit", "Grep", "Glob", "WebFetch", "WebSearch"):
        return name, inp
    return "Other", {"_tool": name, **inp}


class ClaudeCodeAdapter(Adapter):
    name = "claude"
    blocking_events = frozenset({"PreToolUse", "PermissionRequest", "UserPromptSubmit", "UserPromptExpansion",
                                 "Stop", "SubagentStop", "PreCompact", "PostToolBatch", "TaskCreated",
                                 "TaskCompleted", "ConfigChange", "TeammateIdle"})

    def to_canonical(self, raw: dict[str, Any]) -> CanonicalEvent:
        ev_name = str(raw.get("hook_event_name") or "Notification")
        if ev_name not in EVENTS:
            ev_name_canon = ev_name   # unknown/new event: keep the name, policies with events=None still see it
        else:
            ev_name_canon = ev_name
        tool = None
        if raw.get("tool_name"):
            cname, cinp = map_tool(raw.get("tool_name"), raw.get("tool_input"))
            tool = make_tool(cname, cinp)
            tool["native_name"] = raw.get("tool_name")
        output, n = (None, 0)
        error = None
        if "tool_response" in raw:
            output, n = response_text(raw.get("tool_response"))
            if ev_name == "PostToolUseFailure":
                error = output[:1000] if output else "tool failed"
            elif isinstance(raw.get("tool_response"), dict):
                tr = raw["tool_response"]
                code = tr.get("exit_code", tr.get("exitCode", tr.get("returnCode")))
                if (isinstance(code, int) and code != 0) or tr.get("is_error") or tr.get("interrupted"):
                    error = f"exit {code}" if code is not None else "error"
        if ev_name == "PostToolUseFailure" and error is None:
            error = str(raw.get("error") or "tool failed")
        extra = {k: v for k, v in raw.items() if k not in _COMMON}
        return CanonicalEvent(
            event=ev_name_canon, harness="claude", harness_version=raw.get("version"),
            session={"id": raw.get("session_id") or "unknown", "cwd": raw.get("cwd"),
                     "transcript_path": raw.get("transcript_path"), "permission_mode": raw.get("permission_mode"),
                     "agent_type": raw.get("agent_type")},
            tool=tool, prompt=raw.get("prompt"), output=output, output_chars=n, error=error,
            duration_ms=raw.get("duration_ms"), agent_id=raw.get("agent_id"), tool_use_id=raw.get("tool_use_id"),
            extra=extra, raw=raw)

    def to_native(self, event: CanonicalEvent, decision: Decision) -> NativeResponse:
        ev, kind, reason = event.event, decision.kind, decision.reason or ""
        if kind == DENY:
            reason = f"[DinoGuard:{decision.policy}] {reason}" if decision.policy else f"[DinoGuard] {reason}"
            if ev == "PreToolUse":
                return NativeResponse(0, json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse", "permissionDecision": "deny",
                    "permissionDecisionReason": reason}}), "")
            if ev == "PermissionRequest":
                return NativeResponse(0, json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PermissionRequest", "decision": {"behavior": "deny", "message": reason}}}), "")
            if ev in ("Stop", "SubagentStop"):
                return NativeResponse(0, json.dumps({"decision": "block", "reason": reason}), "")
            if ev in ("UserPromptSubmit", "UserPromptExpansion"):
                return NativeResponse(0, json.dumps({"decision": "block", "reason": reason}), "")
            if ev in ("PostToolUse", "PostToolUseFailure"):
                # cannot undo side effects; tell the agent
                return NativeResponse(0, json.dumps({"decision": "block", "reason": reason}), "")
            if ev in self.blocking_events:
                return NativeResponse(2, "", reason)
            return NativeResponse(0, "", "")
        if kind == INSTRUCT:
            text = f"[DinoGuard] {reason}"
            if ev in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "UserPromptSubmit", "SessionStart",
                      "Stop", "SubagentStop"):
                return NativeResponse(0, json.dumps({"hookSpecificOutput": {
                    "hookEventName": ev, "additionalContext": text}}), "")
            return NativeResponse(0, "", "")
        return NativeResponse(0, "", "")

    def fail_closed(self, event_name: str | None, reason: str) -> NativeResponse:
        reason = f"[DinoGuard] {reason}"
        if event_name == "PreToolUse":
            return NativeResponse(2, json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": reason}}), reason)
        if event_name == "PermissionRequest":
            return NativeResponse(0, json.dumps({"hookSpecificOutput": {
                "hookEventName": "PermissionRequest", "decision": {"behavior": "deny", "message": reason}}}), "")
        if event_name in ("Stop", "SubagentStop"):
            # Never trap the agent in an endless turn because the guard is down: let it stop.
            return NativeResponse(0, json.dumps({"systemMessage": reason}), "")
        return NativeResponse(2, "", reason)
