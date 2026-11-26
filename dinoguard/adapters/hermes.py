"""Hermes Agent (Nous Research) shell hooks. Verified 2026-11-26 against hermes-agent.nousresearch.com/docs
(features/hooks, reference/tools-reference) and tools/*.py on main. See docs/adapters/hermes.md.

stdin: {"hook_event_name", "tool_name", "tool_input", "session_id", "cwd", "profile", "extra"}
block: stdout {"decision": "block", "reason": "..."}; pre_llm_call context: {"context": "..."}.
Hooks must be registered with `fail_closed: true` or a crash/timeout lets the tool run.
"""
from __future__ import annotations

import json

from ..engine.decisions import DENY, INSTRUCT, Decision
from ..engine.events import CanonicalEvent, make_tool
from .base import Adapter, NativeResponse, response_text
from .common import canonical_input

EVENT_MAP = {"pre_tool_call": "PreToolUse", "post_tool_call": "PostToolUse", "pre_llm_call": "ModelRequest",
             "post_llm_call": "ModelResponse", "on_session_start": "SessionStart", "on_session_end": "SessionEnd",
             "subagent_start": "SubagentStart", "subagent_stop": "SubagentStop"}
TOOLS = {"terminal": "Bash", "read_file": "Read", "write_file": "Write", "patch": "Edit", "search_files": "Grep",
         "web_extract": "WebFetch", "web_search": "WebSearch", "delegate_task": "Task"}


def map_tool(name: str | None, inp) -> tuple[str, dict]:
    args = dict(inp) if isinstance(inp, dict) else {}
    canon = TOOLS.get(str(name or ""))
    if not canon:
        if str(name or "").startswith("mcp_"):
            server, _, tool = str(name)[4:].partition("_")
            return f"MCP:{server}:{tool or '?'}", args
        return "Other", {"_tool": name, **args}
    out = canonical_input(canon, args)
    if canon == "Edit" and args.get("mode") == "patch":
        out["new_string"] = str(args.get("patch") or "")
    return canon, out


class HermesAdapter(Adapter):
    name = "hermes"
    VERIFIED_VERSION = "docs read 2026-11-26 (main branch)"
    blocking_events = frozenset({"PreToolUse"})
    known_native_tools = frozenset({"process", "todo", "memory", "clarify", "skill_view", "skills_list",
                                    "session_search", "vision_analyze", "image_generate", "text_to_speech",
                                    "execute_code", "cronjob", "send_message"})

    def to_canonical(self, raw: dict) -> CanonicalEvent:
        native = str(raw.get("hook_event_name") or "")
        tool = None
        if raw.get("tool_name"):
            cname, cinp = map_tool(raw.get("tool_name"), raw.get("tool_input"))
            tool = make_tool(cname, cinp)
            tool["native_name"] = raw.get("tool_name")
        extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
        output, n = (None, 0)
        if "result" in extra or "tool_result" in extra:
            output, n = response_text(extra.get("result", extra.get("tool_result")))
        tin = raw.get("tool_input") if isinstance(raw.get("tool_input"), dict) else {}
        cwd = raw.get("cwd") or tin.get("workdir")
        return CanonicalEvent(event=EVENT_MAP.get(native, native or "Notification"), harness=self.name,
                              session={"id": raw.get("session_id") or "hermes", "cwd": cwd,
                                       "profile": raw.get("profile")},
                              tool=tool, prompt=extra.get("user_message") if native == "pre_llm_call" else None,
                              output=output, output_chars=n, extra={"native_event": native, **extra}, raw=raw)

    def to_native(self, event: CanonicalEvent, decision: Decision) -> NativeResponse:
        if decision.kind == DENY and event.event == "PreToolUse":
            return NativeResponse(0, json.dumps({"decision": "block",
                                                 "reason": f"[DinoGuard:{decision.policy}] {decision.reason or ''}"}), "")
        if decision.kind == INSTRUCT and event.extra.get("native_event") == "pre_llm_call":
            return NativeResponse(0, json.dumps({"context": f"[DinoGuard] {decision.reason or ''}"}), "")
        return NativeResponse(0, "{}", "")

    def fail_closed(self, event_name, reason):
        if event_name in ("pre_tool_call", "PreToolUse", None):
            return NativeResponse(0, json.dumps({"decision": "block", "reason": f"[DinoGuard] {reason}"}), "")
        return NativeResponse(0, "{}", "")


SNIPPET = """# DinoGuard hooks for Hermes Agent: add under the top level of your profile's config.yaml
hooks:
  pre_tool_call:
    - matcher: ".*"
      command: "{cmd}"
      timeout: 30
      fail_closed: true        # without this a DinoGuard crash or timeout lets the tool run
  post_tool_call:
    - matcher: ".*"
      command: "{cmd}"
      timeout: 30
  pre_llm_call:
    - command: "{cmd}"
      timeout: 30
  on_session_start:
    - command: "{cmd}"
  on_session_end:
    - command: "{cmd}"
# Then approve the hook once (`hermes hooks list`), or set hooks_auto_accept / --accept-hooks for gateway and CI.
"""
