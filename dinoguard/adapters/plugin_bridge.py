"""Pi and OpenClaw have no shell-command hooks, only in-process TypeScript extensions/plugins. DinoGuard installs
a tiny shim (PI_EXTENSION / OPENCLAW_PLUGIN below) that forwards each call to `dinoguard-hook <harness>` and maps
the answer back. Verified 2026-11-26: Pi @earendil-works/pi-coding-agent 1.6.1 docs/extensions.md; OpenClaw
docs.openclaw.ai/plugins/hooks*. See docs/adapters/pi.md and docs/adapters/openclaw.md.

Wire format (ours, written by the shim):
  {"event": "PreToolUse"|"PostToolUse"|"UserPromptSubmit"|"SessionStart"|"SessionEnd",
   "tool": "<native name>", "input": {...}, "session_id": "...", "cwd": "...", "output": "...", "error": "..."}
Response: {"decision": "allow"|"instruct"|"deny", "reason": "...", "policy": "..."}. Both shims block when
dinoguard-hook can't be run or answers garbage (fail closed).
"""
from __future__ import annotations

import json
import os

from ..engine.decisions import DENY, Decision
from ..engine.events import CanonicalEvent, make_tool
from .base import Adapter, NativeResponse
from .claude_like import _mapped


class _Bridge(Adapter):
    TOOLS: dict[str, str] = {}

    def map_tool(self, name, inp):
        inp = dict(inp) if isinstance(inp, dict) else {}
        return _mapped(name, inp, self.TOOLS)

    def to_canonical(self, raw: dict) -> CanonicalEvent:
        tool = None
        if raw.get("tool"):
            cname, cinp = self.map_tool(raw.get("tool"), raw.get("input"))
            tool = make_tool(cname, cinp)
            tool["native_name"] = raw.get("tool")
        out = raw.get("output")
        out = out if out is None or isinstance(out, str) else json.dumps(out, default=str)
        return CanonicalEvent(event=str(raw.get("event") or "PreToolUse"), harness=self.name,
                              session={"id": raw.get("session_id") or self.name, "cwd": raw.get("cwd") or os.getcwd(),
                                       "transcript_path": raw.get("transcript_path")},
                              tool=tool, prompt=raw.get("prompt"), output=out, output_chars=len(out or ""),
                              error=raw.get("error"), tool_use_id=raw.get("tool_use_id"), raw=raw)

    def to_native(self, event, decision: Decision) -> NativeResponse:
        reason = decision.reason or ""
        if decision.kind == DENY:
            reason = f"[DinoGuard:{decision.policy}] {reason}"
        elif reason:
            reason = f"[DinoGuard] {reason}"
        return NativeResponse(0, json.dumps({"decision": decision.kind, "reason": reason or None,
                                             "policy": decision.policy}), "")

    def fail_closed(self, event_name, reason):
        return NativeResponse(0, json.dumps({"decision": DENY, "reason": f"[DinoGuard] {reason}",
                                             "policy": "dinoguard/unavailable"}), "")


class PiAdapter(_Bridge):
    name = "pi"
    VERIFIED_VERSION = "pi-coding-agent 1.6.1 (2026-11-25)"
    blocking_events = frozenset({"PreToolUse"})
    TOOLS = {"bash": "Bash", "powershell": "Bash", "read": "Read", "write": "Write", "edit": "Edit", "grep": "Grep",
             "find": "Glob", "ls": "Glob"}
    known_native_tools = frozenset()

    def map_tool(self, name, inp):
        inp = dict(inp) if isinstance(inp, dict) else {}
        if name == "edit" and isinstance(inp.get("edits"), list):     # {path, edits: [{oldText, newText}]}
            inp["old_string"] = "\n".join(str(e.get("oldText", "")) for e in inp["edits"] if isinstance(e, dict))
            inp["new_string"] = "\n".join(str(e.get("newText", "")) for e in inp["edits"] if isinstance(e, dict))
        return _mapped(name, inp, self.TOOLS)


class OpenClawAdapter(_Bridge):
    name = "openclaw"
    VERIFIED_VERSION = "docs read 2026-11-26 (npm openclaw 2026.11.10); read/write/edit field names unverified"
    blocking_events = frozenset({"PreToolUse"})
    TOOLS = {"exec": "Bash", "apply_patch": "Edit", "read": "Read", "write": "Write", "edit": "Edit",
             "web_fetch": "WebFetch", "web_search": "WebSearch"}
    known_native_tools = frozenset({"process", "message", "cron", "sessions_spawn", "sessions_send", "browser",
                                    "canvas", "nodes", "image"})


_SPAWN_JS = r"""
import { spawnSync } from "node:child_process";
const HOOK = __HOOK__;
function ask(harness, payload) {
  const r = spawnSync(HOOK[0], [...HOOK.slice(1), harness], { input: JSON.stringify(payload), encoding: "utf8", timeout: 180000, windowsHide: true });
  if (r.error || r.status !== 0) return { decision: "deny", reason: "[DinoGuard] could not run dinoguard-hook (" + (r.error ? r.error.message : "exit " + r.status) + "); run `dinoguard doctor`." };
  try { const d = JSON.parse(r.stdout || ""); return d && d.decision ? d : { decision: "deny", reason: "[DinoGuard] empty answer; run `dinoguard doctor`." }; }
  catch { return { decision: "deny", reason: "[DinoGuard] unreadable answer; run `dinoguard doctor`." }; }
}
"""

PI_EXTENSION = "// DinoGuard extension for Pi (generated by `dinoguard install --harness pi`).\n" + _SPAWN_JS + r"""
export default function (pi) {
  const sid = (ctx) => { try { return ctx.sessionManager.getSessionId(); } catch { return "pi"; } };
  pi.on("tool_call", async (event, ctx) => {
    const d = ask("pi", { event: "PreToolUse", tool: event.toolName, input: event.input, tool_use_id: event.toolCallId,
                          session_id: sid(ctx), cwd: ctx.cwd });
    if (d.decision === "deny") return { block: true, reason: d.reason || "Blocked by DinoGuard" };
    if (d.decision === "instruct" && d.reason) {
      try { pi.sendMessage({ customType: "dinoguard", content: d.reason, display: true }, { deliverAs: "steer" }); } catch {}
    }
  });
  pi.on("tool_result", async (event, ctx) => {
    const text = Array.isArray(event.content) ? event.content.map(c => c.text || "").join("\n") : String(event.content ?? "");
    ask("pi", { event: event.isError ? "PostToolUseFailure" : "PostToolUse", tool: event.toolName, input: event.input,
                tool_use_id: event.toolCallId, output: text.slice(0, 16000), error: event.isError ? text.slice(0, 1000) : null,
                session_id: sid(ctx), cwd: ctx.cwd });
  });
  pi.on("session_start", async (_e, ctx) => { ask("pi", { event: "SessionStart", session_id: sid(ctx), cwd: ctx.cwd }); });
  pi.on("session_shutdown", async (_e, ctx) => { ask("pi", { event: "SessionEnd", session_id: sid(ctx), cwd: ctx.cwd }); });
}
"""

OPENCLAW_PLUGIN = "// DinoGuard plugin for OpenClaw (generated by `dinoguard install --harness openclaw`).\n" + _SPAWN_JS + r"""
export default {
  id: "dinoguard", name: "DinoGuard", description: "Guardrails: checks every tool call before it runs.",
  register(api) {
    api.on("before_tool_call", (event, ctx) => {
      const d = ask("openclaw", { event: "PreToolUse", tool: event.toolName, input: event.params || {},
                                  tool_use_id: event.toolCallId, session_id: ctx.sessionId || ctx.sessionKey, cwd: process.cwd() });
      if (d.decision === "deny") return { block: true, blockReason: d.reason || "Blocked by DinoGuard" };
      return {};
    });
    api.on("after_tool_call", (event, ctx) => {
      ask("openclaw", { event: event.error ? "PostToolUseFailure" : "PostToolUse", tool: event.toolName, input: event.params || {},
                        tool_use_id: event.toolCallId, output: String(event.result ?? "").slice(0, 16000),
                        error: event.error ? String(event.error).slice(0, 1000) : null,
                        session_id: ctx.sessionId || ctx.sessionKey, cwd: process.cwd() });
    });
  },
};
"""

OPENCLAW_MANIFEST = {"id": "dinoguard", "name": "DinoGuard", "description": "Checks every tool call before it runs.",
                     "configSchema": {"type": "object", "additionalProperties": False, "properties": {}}}
OPENCLAW_PACKAGE = {"name": "dinoguard-openclaw", "version": "1.0.0", "type": "module",
                    "openclaw": {"extensions": ["./index.js"]}}
