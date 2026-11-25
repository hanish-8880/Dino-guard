"""Gemini CLI hooks adapter. Verified against https://geminicli.com/docs/hooks/reference/ (docs/adapters/gemini.md)."""
from __future__ import annotations

import json

from ..engine.decisions import DENY, INSTRUCT
from ..engine.events import CanonicalEvent, make_tool
from .base import Adapter, NativeResponse, response_text
from .common import map_generic

EVENT_MAP = {"BeforeTool": "PreToolUse", "AfterTool": "PostToolUse", "BeforeAgent": "UserPromptSubmit",
             "AfterAgent": "Stop", "SessionStart": "SessionStart", "SessionEnd": "SessionEnd",
             "PreCompress": "PreCompact", "Notification": "Notification"}


class GeminiAdapter(Adapter):
    name = "gemini"
    blocking_events = frozenset({"PreToolUse", "UserPromptSubmit"})

    def to_canonical(self, raw: dict) -> CanonicalEvent:
        native = str(raw.get("hook_event_name") or "")
        ev = EVENT_MAP.get(native, native or "Notification")
        tool = None
        if raw.get("tool_name"):
            name, inp = map_generic(raw["tool_name"], raw.get("tool_input"), mcp_prefix="mcp_")
            tool = make_tool(name, inp)
        tr = raw.get("tool_response") or {}
        out, n = response_text(tr.get("llmContent") if isinstance(tr, dict) else tr)
        err = tr.get("error") if isinstance(tr, dict) else None
        return CanonicalEvent(
            event="PostToolUseFailure" if ev == "PostToolUse" and err else ev, harness="gemini",
            session={"id": raw.get("session_id") or "unknown", "cwd": raw.get("cwd"),
                     "transcript_path": raw.get("transcript_path")},
            tool=tool, prompt=raw.get("prompt"), output=out or None, output_chars=n,
            error=str(err) if err else None,
            extra={"native_event": native, "stop_hook_active": raw.get("stop_hook_active")}, raw=raw)

    def to_native(self, event, decision) -> NativeResponse:
        native = event.extra.get("native_event", "")
        if decision.kind == DENY and native in ("BeforeTool", "BeforeAgent"):
            return NativeResponse(0, json.dumps({"decision": "deny",
                                                 "reason": f"[DinoGuard:{decision.policy}] {decision.reason}"}), "")
        if decision.kind == DENY and native == "AfterTool":
            return NativeResponse(0, json.dumps({"hookSpecificOutput": {
                "additionalContext": f"[DinoGuard:{decision.policy}] {decision.reason}"}}), "")
        if decision.kind == INSTRUCT and native in ("AfterTool", "BeforeAgent", "SessionStart"):
            return NativeResponse(0, json.dumps({"hookSpecificOutput": {
                "additionalContext": f"[DinoGuard] {decision.reason}"}}), "")
        if decision.kind == INSTRUCT and native == "BeforeTool":
            return NativeResponse(0, json.dumps({"systemMessage": f"[DinoGuard] {decision.reason}"}), "")
        return NativeResponse(0, "", "")

    def fail_closed(self, event_name, reason):
        if event_name in ("BeforeTool", None):
            return NativeResponse(2, json.dumps({"decision": "deny", "reason": f"[DinoGuard] {reason}"}), reason)
        return NativeResponse(0, "", "")
