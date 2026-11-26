"""SDK adapter + `check()` for custom agents (LangGraph, CrewAI, LlamaIndex, Pydantic AI, your own loop).

Wire format: {"event": "PreToolUse", "tool": "Bash", "input": {...}, "session_id": "...", "cwd": "...",
              "harness": "my-agent", "output": "..."}  ->  stdout {"decision", "reason", "policy"}
"""
from __future__ import annotations

import json
import os

from ..engine.decisions import DENY, Decision
from ..engine.events import CanonicalEvent, make_tool
from .base import Adapter, NativeResponse
from .common import map_generic


class SDKAdapter(Adapter):
    name = "sdk"
    VERIFIED_VERSION = "dinoguard SDK (same package version)"
    blocking_events = frozenset({"PreToolUse", "UserPromptSubmit", "Stop", "PermissionRequest"})

    def to_canonical(self, raw: dict) -> CanonicalEvent:
        tool = None
        if raw.get("tool"):
            name, inp = map_generic(raw["tool"], raw.get("input"))
            tool = make_tool(name, inp)
        session = {"id": raw.get("session_id") or "sdk", "cwd": raw.get("cwd") or os.getcwd()}
        if raw.get("environment"):
            session["environment"] = str(raw["environment"])[:64]
        extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
        output = raw.get("output")
        output = output if output is None or isinstance(output, str) else json.dumps(output, default=str)
        dur = raw.get("duration_ms")
        return CanonicalEvent(event=str(raw.get("event") or "PreToolUse"), harness=str(raw.get("harness") or self.name),
                              session=session, tool=tool, prompt=raw.get("prompt"), output=output,
                              output_chars=len(output or ""), error=raw.get("error"),
                              duration_ms=int(dur) if isinstance(dur, (int, float)) else None,
                              agent_id=raw.get("agent_id"), tool_use_id=raw.get("tool_use_id"), extra=extra, raw=raw)

    def to_native(self, event, decision: Decision) -> NativeResponse:
        return NativeResponse(0, json.dumps({"decision": decision.kind, "reason": decision.reason,
                                             "policy": decision.policy}), "")

    def fail_closed(self, event_name, reason):
        return NativeResponse(0, json.dumps({"decision": DENY, "reason": f"[DinoGuard] {reason}",
                                             "policy": "dinoguard/unavailable"}), "")


def evaluate_event(event: CanonicalEvent) -> Decision:
    """Daemon if configured (fail closed), else in-process."""
    from .. import config
    cfg = config.load()
    if (cfg.get("daemon") or {}).get("enabled"):
        from ..daemon import client as dclient
        try:
            return dclient.evaluate(event, timeout=dclient.timeout_for(cfg))
        except dclient.DaemonError as e:
            return Decision(DENY, f"DinoGuard daemon problem: {e}. Run `dinoguard doctor`.",
                            policy="dinoguard/unavailable")
    from ..service import Service
    return Service(cfg=cfg).decide(event)


def check(tool: str | None, input: dict | None, session_id: str | None = None, event: str = "PreToolUse",  # noqa: A002
          cwd: str | None = None, harness: str = "sdk", prompt: str | None = None, output: str | None = None,
          error: str | None = None, agent_id: str | None = None, **more) -> Decision:
    """`more` may carry environment, extra, duration_ms, tool_use_id (see the wire format above)."""
    ev = SDKAdapter().to_canonical({"event": event, "tool": tool, "input": input, "session_id": session_id,
                                    "cwd": cwd, "harness": harness, "prompt": prompt, "output": output,
                                    "error": error, "agent_id": agent_id, **more})
    return evaluate_event(ev)
