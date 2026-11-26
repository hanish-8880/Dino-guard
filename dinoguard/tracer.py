"""SDK lifecycle tracer for custom agents (CLAUDE.md §17.9).

    import dinoguard

    with dinoguard.trace_session(agent_id="support-bot", environment="staging") as t:
        t.prompt("refund order 42")
        t.model_request("claude-sonnet-5-5", messages=3)
        t.model_response("claude-sonnet-5-5", tokens_in=1200, tokens_out=300, latency_ms=900)
        d = t.tool_call("Bash", {"command": "ls"})        # can be denied -> DinoGuardDenied (or check d.kind)
        t.tool_result("Bash", {"command": "ls"}, output="a.txt", duration_ms=12)

Every call becomes a canonical event evaluated like a hook event (daemon if configured, fail-closed), so the
same policies, store, dashboard and runner see SDK agents. Lifecycle events are observe-only.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Callable

from .adapters.sdk import check
from .engine.decisions import Decision
from .integrations import DinoGuardDenied

__all__ = ["Tracer", "trace_session", "DinoGuardDenied"]


class Tracer:
    def __init__(self, session_id: str | None = None, agent_id: str | None = None, environment: str | None = None,
                 harness: str = "sdk", cwd: str | None = None, raise_on_deny: bool = True,
                 send: Callable[..., Decision] | None = None):
        self.session_id = session_id or f"sdk-{uuid.uuid4().hex[:16]}"
        self.agent_id, self.environment, self.harness = agent_id, environment, harness
        self.cwd = cwd or os.getcwd()
        self.raise_on_deny = raise_on_deny
        self._send = send or check
        self._calls = 0
        self._hooks: dict[str, float] = {}

    # -- plumbing ---------------------------------------------------------------------------------------
    def event(self, name: str, tool: str | None = None, input: dict | None = None, **fields) -> Decision:  # noqa: A002
        extra = {k: v for k, v in (fields.pop("extra", None) or {}).items() if v is not None}
        return self._send(tool, input, session_id=self.session_id, event=name, cwd=self.cwd, harness=self.harness,
                          agent_id=self.agent_id, environment=self.environment, extra=extra, **fields)

    def _gate(self, d: Decision) -> Decision:
        if d.kind == "deny" and self.raise_on_deny:
            raise DinoGuardDenied(d)
        return d

    # -- session ------------------------------------------------------------------------------------------
    def start(self, **meta) -> Decision:
        return self.event("SessionStart", extra=meta)

    def end(self, status: str = "completed", **meta) -> Decision:
        return self.event("SessionEnd", extra={"status": status, **meta})

    def __enter__(self) -> "Tracer":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None and not isinstance(exc, DinoGuardDenied):
            self.error(exc)
        self.end("failed" if exc is not None else "completed")
        return False

    # -- prompts / turns ------------------------------------------------------------------------------------
    def prompt(self, text: str) -> Decision:
        return self._gate(self.event("UserPromptSubmit", prompt=text))

    def stop(self, final: str | None = None) -> Decision:
        """Turn end. A deny here means a workflow gate says "not done yet" (returned, never raised)."""
        return self.event("Stop", extra={"final": (final or "")[:2000]})

    # -- model ------------------------------------------------------------------------------------------------
    def model_request(self, model: str, **meta) -> Decision:
        return self.event("ModelRequest", extra={"model": model, **meta})

    def model_response(self, model: str, tokens_in: int = 0, tokens_out: int = 0, cache_read: int = 0,
                       cache_write: int = 0, cost_usd: float | None = None, latency_ms: float | None = None,
                       **meta) -> Decision:
        return self.event("ModelResponse", extra={"model": model, "tokens_in": tokens_in, "tokens_out": tokens_out,
                                                  "cache_read": cache_read, "cache_write": cache_write,
                                                  "cost_usd": cost_usd, "latency_ms": latency_ms, **meta})

    # -- tools ------------------------------------------------------------------------------------------------
    def tool_call(self, tool: str, input: dict | None = None, tool_use_id: str | None = None) -> Decision:  # noqa: A002
        self._calls += 1
        return self._gate(self.event("PreToolUse", tool, input or {}, tool_use_id=tool_use_id or self._tuid(tool)))

    def tool_result(self, tool: str, input: dict | None = None, output: Any = None, error: str | None = None,  # noqa: A002
                    duration_ms: float | None = None, tool_use_id: str | None = None) -> Decision:
        out = None if output is None else (output if isinstance(output, str) else repr(output))
        return self.event("PostToolUseFailure" if error else "PostToolUse", tool, input or {},
                          output=(out or "")[:16000] if out is not None else None, error=error,
                          duration_ms=duration_ms, tool_use_id=tool_use_id or self._tuid(tool))

    def _tuid(self, tool: str) -> str:
        return f"{self.session_id}:{self._calls}:{tool}"

    def run_tool(self, tool: str, input: dict, fn: Callable[[], Any]) -> Any:  # noqa: A002
        """check -> run -> record result, timing and errors in one call."""
        self.tool_call(tool, input)
        t0 = time.perf_counter()
        try:
            res = fn()
        except Exception as e:
            self.tool_result(tool, input, error=f"{type(e).__name__}: {e}", duration_ms=(time.perf_counter() - t0) * 1000)
            raise
        self.tool_result(tool, input, output=res, duration_ms=(time.perf_counter() - t0) * 1000)
        return res

    # -- hooks, humans, control ---------------------------------------------------------------------------
    def hook_start(self, name: str, **meta) -> Decision:
        self._hooks[name] = time.perf_counter()
        return self.event("HookStart", extra={"hook": name, **meta})

    def hook_end(self, name: str, **meta) -> Decision:
        t0 = self._hooks.pop(name, None)
        dur = (time.perf_counter() - t0) * 1000 if t0 is not None else None
        return self.event("HookEnd", extra={"hook": name, "duration_ms": dur, **meta})

    def human_wait(self, reason: str = "", **meta) -> Decision:
        return self.event("HumanWait", extra={"reason": reason, **meta})

    def human_input(self, text: str = "", **meta) -> Decision:
        return self.event("HumanInput", prompt=text or None, extra=meta)

    def interruption(self, reason: str = "", **meta) -> Decision:
        return self.event("Interruption", extra={"reason": reason, **meta})

    def error(self, err: BaseException | str, **meta) -> Decision:
        msg = f"{type(err).__name__}: {err}" if isinstance(err, BaseException) else str(err)
        return self.event("AgentError", error=msg[:1000], extra=meta)

    def pause(self, reason: str = "", **meta) -> Decision:
        return self.event("AgentPause", extra={"reason": reason, **meta})

    def resume(self, **meta) -> Decision:
        return self.event("AgentResume", extra=meta)


def trace_session(**kw) -> Tracer:
    return Tracer(**kw)
