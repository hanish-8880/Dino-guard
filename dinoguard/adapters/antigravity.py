"""Google Antigravity CLI (`agy`) hooks. Verified 2026-11-26 against antigravity.google/docs/hooks.md.
See docs/adapters/antigravity.md.

The payload has no event-name field, so the installed command passes it: `dinoguard-hook antigravity PreToolUse`.
stdin (camelCase): conversationId, workspacePaths, transcriptPath, modelName, lastUserInput, toolCall{name,args},
stepIdx, error, terminationReason ...  Decisions go back as JSON on stdout (exit codes are not documented), so
DinoGuard always prints JSON and exits 0, including when it fails closed.
"""
from __future__ import annotations

import json

from ..engine.decisions import DENY, INSTRUCT, Decision
from ..engine.events import CanonicalEvent, make_tool
from .base import Adapter, NativeResponse, response_text
from .common import canonical_input

EVENT_MAP = {"PreToolUse": "PreToolUse", "PostToolUse": "PostToolUse", "PreInvocation": "ModelRequest",
             "PostInvocation": "ModelResponse", "Stop": "Stop", "SessionStart": "SessionStart"}
HOOK_EVENTS = ["PreToolUse", "PostToolUse", "PreInvocation", "PostInvocation", "Stop", "SessionStart"]

# native tool -> (canonical, {native arg -> canonical arg})
TOOLS = {
    "run_command": ("Bash", {"CommandLine": "command", "Cwd": "cwd"}),
    "view_file": ("Read", {"AbsolutePath": "file_path"}),
    "write_to_file": ("Write", {"TargetFile": "file_path", "CodeContent": "content"}),
    "replace_file_content": ("Edit", {"TargetFile": "file_path", "TargetContent": "old_string",
                                      "ReplacementContent": "new_string"}),
    "multi_replace_file_content": ("Edit", {"TargetFile": "file_path"}),
    "grep_search": ("Grep", {"Query": "pattern", "SearchPath": "path"}),
    "find_by_name": ("Glob", {"Pattern": "pattern", "SearchDirectory": "path"}),
    "list_dir": ("Glob", {"DirectoryPath": "path"}),
    "read_url_content": ("WebFetch", {"Url": "url"}),
    "search_web": ("WebSearch", {"query": "query", "Query": "query"}),
    "invoke_subagent": ("Task", {}),
}


def map_tool(name: str | None, args) -> tuple[str, dict]:
    args = dict(args) if isinstance(args, dict) else {}
    spec = TOOLS.get(str(name or ""))
    if not spec:
        if str(name or "").startswith("mcp"):
            return f"MCP:{name}", args
        return "Other", {"_tool": name, **args}
    canon, fields = spec
    out = dict(args)
    for src, dst in fields.items():
        if src in args:
            out[dst] = args[src]
    if name == "multi_replace_file_content":
        chunks = args.get("ReplacementChunks") or []
        out["old_string"] = "\n".join(str(c.get("TargetContent", "")) for c in chunks if isinstance(c, dict))
        out["new_string"] = "\n".join(str(c.get("ReplacementContent", "")) for c in chunks if isinstance(c, dict))
    return canon, canonical_input(canon, out) if canon in ("Bash", "Read", "Write", "Edit") else out


class AntigravityAdapter(Adapter):
    name = "antigravity"
    VERIFIED_VERSION = "docs read 2026-11-26 (hooks page undated; App v2.35.2)"
    blocking_events = frozenset({"PreToolUse", "Stop"})
    known_native_tools = frozenset({"manage_task", "schedule", "list_permissions", "ask_permission",
                                    "define_subagent", "send_message", "manage_subagents", "ask_question",
                                    "generate_image"})

    def to_canonical(self, raw: dict) -> CanonicalEvent:
        native = str(raw.get("_event") or raw.get("hookEventName") or "")
        call = raw.get("toolCall") if isinstance(raw.get("toolCall"), dict) else {}
        tool = None
        if call.get("name"):
            cname, cinp = map_tool(call.get("name"), call.get("args"))
            tool = make_tool(cname, cinp)
            tool["native_name"] = call.get("name")
        ws = raw.get("workspacePaths") or []
        cwd = (tool or {}).get("input", {}).get("cwd") or (ws[0] if isinstance(ws, list) and ws else None)
        output, n = response_text(raw.get("toolResult") or raw.get("result")) if native == "PostToolUse" else (None, 0)
        return CanonicalEvent(event=EVENT_MAP.get(native, native or "Notification"), harness=self.name,
                              session={"id": raw.get("conversationId") or "antigravity", "cwd": cwd,
                                       "transcript_path": raw.get("transcriptPath")},
                              tool=tool, prompt=raw.get("lastUserInput") if native == "SessionStart" else None,
                              output=output, output_chars=n,
                              error=str(raw["error"])[:1000] if raw.get("error") else None,
                              extra={"native_event": native, "model": raw.get("modelName"),
                                     "step": raw.get("stepIdx")}, raw=raw)

    def to_native(self, event: CanonicalEvent, decision: Decision) -> NativeResponse:
        ev, kind = event.event, decision.kind
        reason = decision.reason or ""
        if ev == "PreToolUse":
            if kind == DENY:
                return NativeResponse(0, json.dumps({"decision": "deny",
                                                     "reason": f"[DinoGuard:{decision.policy}] {reason}"}), "")
            return NativeResponse(0, json.dumps({"decision": "allow"}), "")     # allow = defer to normal policy
        if ev == "Stop" and kind == DENY:     # a workflow gate: keep going, the reason becomes a system message
            return NativeResponse(0, json.dumps({"decision": "continue", "reason": f"[DinoGuard] {reason}"}), "")
        if kind == INSTRUCT and event.extra.get("native_event") in ("SessionStart", "PreInvocation", "PostInvocation"):
            return NativeResponse(0, json.dumps({"injectSteps": [
                {"systemMessage": {"systemMessage": f"[DinoGuard] {reason}"}}]}), "")
        return NativeResponse(0, "{}", "")

    def fail_closed(self, event_name, reason):
        if event_name in ("PreToolUse", None):
            return NativeResponse(0, json.dumps({"decision": "deny", "reason": f"[DinoGuard] {reason}"}), "")
        return NativeResponse(0, "{}", "")
