"""Harnesses whose hooks copy Claude Code's shape (stdin JSON with hook_event_name / tool_name / tool_input):
Devin CLI, Factory Droid and Deep Agents Code. Verified 2026-11-26, see docs/adapters/{devin,droid,deepagents}.md.

Each one differs in tool names and in which response fields reach the model, so only those parts are overridden.
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..engine.decisions import DENY, INSTRUCT, Decision
from ..engine.events import CanonicalEvent
from .base import NativeResponse
from .claude_code import ClaudeCodeAdapter, map_tool as claude_map_tool
from .codex import _patch_path
from .common import canonical_input


def _mapped(name: str | None, inp: Any, table: dict[str, str]) -> tuple[str, dict]:
    if not name:
        return "Other", dict(inp or {})
    if str(name).startswith("mcp__"):
        return claude_map_tool(name, inp)
    canon = table.get(str(name)) or table.get(str(name).lower())
    args = dict(inp or {}) if isinstance(inp, dict) else {"value": inp}
    if not canon:
        return "Other", {"_tool": name, **args}
    out = canonical_input(canon, args)
    if str(name).lower() in ("apply_patch", "applypatch") and not out.get("file_path"):
        patch = str(args.get("patch") or args.get("input") or "")
        out.update({"file_path": _patch_path(patch), "old_string": "", "new_string": patch})
    return canon, out


class DevinAdapter(ClaudeCodeAdapter):
    name = "devin"
    VERIFIED_VERSION = "docs read 2026-11-26 (CLI v3000.15.14); file-path field names unverified"
    TOOLS = {"exec": "Bash", "read": "Read", "notebook_read": "Read", "write": "Write", "edit": "Edit",
             "notebook_edit": "Edit", "apply_patch": "Edit", "grep": "Grep", "glob": "Glob", "webfetch": "WebFetch",
             "run_subagent": "Task"}
    known_native_tools = frozenset({"get_output", "write_to_process", "kill_shell", "ask_user_question", "todo_write",
                                    "exit_plan_mode", "skill", "read_subagent", "request_scope"})
    blocking_events = frozenset({"PreToolUse", "PermissionRequest", "UserPromptSubmit", "Stop", "SessionStart"})
    CONTEXT_EVENTS = ("UserPromptSubmit", "SessionStart", "PostToolUse")

    def map_tool(self, name, inp):
        return _mapped(name, inp, self.TOOLS)

    def to_canonical(self, raw: dict) -> CanonicalEvent:
        ev = super().to_canonical(raw)
        if not ev.session.get("cwd"):
            ev.session["cwd"] = os.environ.get("DEVIN_PROJECT_DIR") or os.getcwd()
        return ev

    def to_native(self, event: CanonicalEvent, decision: Decision) -> NativeResponse:
        ev, kind = event.event, decision.kind
        if kind == DENY:
            reason = f"[DinoGuard:{decision.policy}] {decision.reason or ''}"
            if ev == "Stop":
                return NativeResponse(0, json.dumps({"decision": "block", "reason": reason}), "")
            if ev in self.blocking_events:
                return NativeResponse(2, "", reason)          # exit 2 is the documented block for every event
            return NativeResponse(0, "", "")
        if kind == INSTRUCT and ev in self.CONTEXT_EVENTS:
            return NativeResponse(0, json.dumps({"hookSpecificOutput": {
                "hookEventName": ev, "additionalContext": f"[DinoGuard] {decision.reason or ''}"}}), "")
        return NativeResponse(0, "", "")

    def fail_closed(self, event_name, reason):
        if event_name == "Stop":
            return NativeResponse(0, "", "")                  # never trap the agent in an endless turn
        return NativeResponse(2, "", f"[DinoGuard] {reason}")


class DroidAdapter(ClaudeCodeAdapter):
    name = "droid"
    VERIFIED_VERSION = "docs read 2026-11-26 (CLI v1.21.0); Edit/Read/Grep field names unverified"
    TOOLS = {"Execute": "Bash", "Read": "Read", "Edit": "Edit", "Create": "Write", "ApplyPatch": "Edit",
             "Glob": "Glob", "Grep": "Grep", "LS": "Glob", "Task": "Task", "FetchUrl": "WebFetch",
             "WebSearch": "WebSearch"}
    known_native_tools = frozenset({"TodoWrite", "ExitSpecMode", "AskUser", "GenerateDroid", "Skill"})
    blocking_events = frozenset({"PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "PreCompact"})
    CONTEXT_EVENTS = ("UserPromptSubmit", "SessionStart")     # additionalContext is NOT sent from PreToolUse/Stop

    def map_tool(self, name, inp):
        return _mapped(name, inp, self.TOOLS)

    def to_canonical(self, raw: dict) -> CanonicalEvent:
        ev = super().to_canonical(raw)
        if not ev.session.get("cwd"):
            ev.session["cwd"] = os.environ.get("FACTORY_PROJECT_DIR") or os.getcwd()
        return ev

    def to_native(self, event: CanonicalEvent, decision: Decision) -> NativeResponse:
        if decision.kind == INSTRUCT:
            if event.event in self.CONTEXT_EVENTS:
                return NativeResponse(0, json.dumps({"hookSpecificOutput": {
                    "hookEventName": event.event, "additionalContext": f"[DinoGuard] {decision.reason or ''}"}}), "")
            return NativeResponse(0, "", "")
        return super().to_native(event, decision)              # deny JSON + Stop block match Claude Code's


class DeepAgentsCodeAdapter(ClaudeCodeAdapter):
    """Deep Agents Code (`dcode`): same tool names and response fields as Claude Code. It fails open on hook
    errors and timeouts (no fail-closed option), which docs/adapters/deepagents.md states plainly."""
    name = "deepagents"
    VERIFIED_VERSION = "docs read 2026-11-26 (deepagents-code 0.1.x); fails open on hook errors"
    blocking_events = frozenset({"PreToolUse", "PermissionRequest", "UserPromptSubmit", "Stop", "PreCompact"})
