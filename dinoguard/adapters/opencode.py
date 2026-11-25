"""OpenCode adapter. OpenCode has no stdin-JSON hooks; DinoGuard ships a JS plugin (written by
`dinoguard install --harness opencode`) that forwards tool calls to `dinoguard-hook opencode` and throws
on deny. Plugin event names / blocking behaviour are only partly documented - see docs/adapters/opencode.md.

Wire format (ours, sent by the plugin):
  {"event": "tool.execute.before"|"tool.execute.after"|"session.idle"|"chat.message",
   "tool": "bash", "args": {...}, "output": "...", "sessionID": "...", "cwd": "..."}
"""
from __future__ import annotations

import json

from ..engine.decisions import DENY, INSTRUCT
from ..engine.events import CanonicalEvent, make_tool
from .base import Adapter, NativeResponse, response_text
from .common import map_generic

EVENT_MAP = {"tool.execute.before": "PreToolUse", "tool.execute.after": "PostToolUse", "session.idle": "Stop",
             "chat.message": "UserPromptSubmit", "session.created": "SessionStart", "session.deleted": "SessionEnd"}

PLUGIN_JS = r"""// DinoGuard plugin for OpenCode (generated). Forwards tool calls to dinoguard-hook; throws on deny.
import { spawnSync } from "node:child_process";
const HOOK = __HOOK__;
function ask(payload) {
  const r = spawnSync(HOOK[0], [...HOOK.slice(1), "opencode"], { input: JSON.stringify(payload), encoding: "utf8", timeout: 180000 });
  if (r.error || (r.status !== 0 && r.status !== 2)) return { decision: "deny", reason: "DinoGuard unreachable; run `dinoguard doctor`." };
  try { return JSON.parse(r.stdout || "{}"); } catch { return { decision: r.status === 2 ? "deny" : "allow", reason: r.stderr }; }
}
export const DinoGuard = async ({ directory }) => ({
  "tool.execute.before": async (input, output) => {
    const d = ask({ event: "tool.execute.before", tool: input.tool, args: output.args, sessionID: input.sessionID, cwd: directory });
    if (d.decision === "deny") throw new Error(d.reason || "Blocked by DinoGuard");
  },
  "tool.execute.after": async (input, output) => {
    ask({ event: "tool.execute.after", tool: input.tool, args: input.args, output: String(output?.output ?? ""), sessionID: input.sessionID, cwd: directory });
  },
});
"""


class OpenCodeAdapter(Adapter):
    name = "opencode"
    blocking_events = frozenset({"PreToolUse"})

    def to_canonical(self, raw: dict) -> CanonicalEvent:
        native = str(raw.get("event") or "")
        ev = EVENT_MAP.get(native, native or "Notification")
        tool = None
        if raw.get("tool"):
            name, inp = map_generic(raw["tool"], raw.get("args"))
            tool = make_tool(name, inp)
        out, n = response_text(raw.get("output"))
        return CanonicalEvent(event=ev, harness="opencode", session={"id": raw.get("sessionID") or "unknown",
                                                                      "cwd": raw.get("cwd")},
                              tool=tool, prompt=raw.get("prompt"), output=out or None, output_chars=n,
                              extra={"native_event": native}, raw=raw)

    def to_native(self, event, decision) -> NativeResponse:
        body = {"decision": decision.kind, "reason": None if decision.kind == "allow"
                else f"[DinoGuard:{decision.policy}] {decision.reason}"}
        return NativeResponse(0, json.dumps(body), "")

    def fail_closed(self, event_name, reason):
        return NativeResponse(0, json.dumps({"decision": DENY, "reason": f"[DinoGuard] {reason}"}), "")


__all__ = ["OpenCodeAdapter", "PLUGIN_JS", "INSTRUCT"]
