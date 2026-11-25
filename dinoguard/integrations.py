"""Framework helpers. No framework is imported here: helpers wrap plain callables / tool objects, so they
work with LangGraph, CrewAI, LlamaIndex, Pydantic AI or a hand-rolled loop.

    from dinoguard.integrations import guard, guard_tools, DinoGuardDenied

    @guard(tool="Bash", input_from=lambda cmd: {"command": cmd})
    def run_shell(cmd): ...
"""
from __future__ import annotations

import functools
import inspect
import uuid
from typing import Any, Callable

from .adapters.sdk import check


class DinoGuardDenied(PermissionError):
    def __init__(self, decision):
        super().__init__(decision.reason or "denied by DinoGuard")
        self.decision = decision


_SESSION = f"sdk-{uuid.uuid4().hex[:12]}"


def _default_input(fn, args, kwargs) -> dict:
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        return {k: v for k, v in bound.arguments.items() if k != "self"}
    except (TypeError, ValueError):
        return {"args": list(args), **kwargs}


def guard(tool: str | None = None, session_id: str | None = None, input_from: Callable | None = None,
          on_instruct: Callable[[str], Any] | None = None, record_output: bool = True, harness: str = "sdk"):
    """Decorator: check before the call (raise DinoGuardDenied on deny), report output after."""
    def deco(fn):
        name = tool or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            inp = input_from(*args, **kwargs) if input_from else _default_input(fn, args, kwargs)
            sid = session_id or _SESSION
            d = check(name, inp, session_id=sid, harness=harness)
            if d.kind == "deny":
                raise DinoGuardDenied(d)
            if d.kind == "instruct" and on_instruct:
                on_instruct(d.reason or "")
            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                check(name, inp, session_id=sid, event="PostToolUseFailure", error=str(e)[:1000], harness=harness)
                raise
            if record_output:
                check(name, inp, session_id=sid, event="PostToolUse", output=str(result)[:16000], harness=harness)
            return result
        return wrapper
    return deco


def guard_tools(tools: list, attr_candidates=("func", "_run", "fn", "run", "invoke"), **kw) -> list:
    """Wrap the callable inside tool objects (LangChain/LangGraph Tool.func, CrewAI BaseTool._run,
    LlamaIndex FunctionTool.fn ...). Objects are modified in place and returned."""
    for t in tools:
        name = getattr(t, "name", None) or type(t).__name__
        for attr in attr_candidates:
            f = getattr(t, attr, None)
            if callable(f) and not getattr(f, "__dinoguard__", False):
                g = guard(tool=name, **kw)(f)
                g.__dinoguard__ = True
                try:
                    setattr(t, attr, g)
                except (AttributeError, TypeError):
                    object.__setattr__(t, attr, g)
                break
    return tools


def langgraph_tool_node_guard(tool_call: dict, session_id: str | None = None):
    """For LangGraph ToolNode-style dicts {"name", "args"}: returns None if allowed, else an error message
    string the graph can feed back to the model instead of executing."""
    d = check(tool_call.get("name", "Other"), tool_call.get("args") or {}, session_id=session_id or _SESSION,
              harness="langgraph")
    return None if d.kind != "deny" else f"Blocked by DinoGuard: {d.reason}"
