"""Framework helpers over the SDK tracer (CLAUDE.md §17.9). No framework is imported unless you use its helper.

    from dinoguard import trace_session
    from dinoguard.frameworks import langchain_handler, traced_tools, pydantic_ai_tool

    t = trace_session(agent_id="research-bot")
    agent.invoke(..., config={"callbacks": [langchain_handler(t)]})     # LangChain / LangGraph
    traced_tools(llama_tools, t)                                         # LlamaIndex FunctionTool.fn
    traced_tools(crew_tools, t)                                          # CrewAI BaseTool._run
    @agent.tool
    @pydantic_ai_tool(t)                                                 # Pydantic AI
    def search(ctx, q: str): ...
"""
from __future__ import annotations

import functools
import inspect
import time
from typing import Any

from .tracer import DinoGuardDenied, Tracer

__all__ = ["langchain_handler", "LangChainCallbacks", "traced_tools", "pydantic_ai_tool", "crewai_step_callback",
           "deepagents_middleware", "guard_tool_request", "DinoGuardDenied"]


def _usage(response: Any) -> dict:
    """Token usage from a LangChain LLMResult (llm_output.token_usage or generation usage_metadata)."""
    out = {"tokens_in": 0, "tokens_out": 0}
    llm_out = getattr(response, "llm_output", None) or {}
    tu = (llm_out.get("token_usage") or llm_out.get("usage") or {}) if isinstance(llm_out, dict) else {}
    out["tokens_in"] = int(tu.get("prompt_tokens") or tu.get("input_tokens") or 0)
    out["tokens_out"] = int(tu.get("completion_tokens") or tu.get("output_tokens") or 0)
    model = llm_out.get("model_name") or llm_out.get("model") if isinstance(llm_out, dict) else None
    if not (out["tokens_in"] or out["tokens_out"]):
        for gens in getattr(response, "generations", None) or []:
            for g in gens or []:
                um = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                out["tokens_in"] += int(um.get("input_tokens") or 0)
                out["tokens_out"] += int(um.get("output_tokens") or 0)
                meta = getattr(getattr(g, "message", None), "response_metadata", None) or {}
                model = model or meta.get("model_name") or meta.get("model")
    out["model"] = model
    return out


class LangChainCallbacks:
    """Duck-typed LangChain callback handler: model calls, tool calls (deniable), tool results and errors."""
    raise_error = True          # so a DinoGuard deny stops the tool instead of being logged and ignored
    run_inline = True
    ignore_llm = ignore_chain = ignore_agent = ignore_retriever = ignore_chat_model = ignore_retry = False
    ignore_custom_event = True

    def __init__(self, tracer: Tracer, default_model: str = "unknown"):
        self.t = tracer
        self.default_model = default_model
        self._llm: dict[Any, tuple[float, str]] = {}
        self._tools: dict[Any, tuple[float, str, dict]] = {}

    @staticmethod
    def _model(serialized: dict | None, kw: dict) -> str | None:
        inv = kw.get("invocation_params") or {}
        return inv.get("model") or inv.get("model_name") or ((serialized or {}).get("kwargs") or {}).get("model")

    def on_llm_start(self, serialized, prompts, *, run_id=None, **kw):
        model = self._model(serialized, kw) or self.default_model
        self._llm[run_id] = (time.perf_counter(), model)
        self.t.model_request(model, prompts=len(prompts or []))

    def on_chat_model_start(self, serialized, messages, *, run_id=None, **kw):
        model = self._model(serialized, kw) or self.default_model
        self._llm[run_id] = (time.perf_counter(), model)
        self.t.model_request(model, messages=sum(len(m) for m in messages or []))

    def on_llm_end(self, response, *, run_id=None, **kw):
        t0, model = self._llm.pop(run_id, (None, self.default_model))
        u = _usage(response)
        self.t.model_response(u.pop("model") or model, latency_ms=(time.perf_counter() - t0) * 1000 if t0 else None, **u)

    def on_llm_error(self, error, *, run_id=None, **kw):
        self._llm.pop(run_id, None)
        self.t.error(error, where="llm")

    def on_tool_start(self, serialized, input_str, *, run_id=None, inputs=None, **kw):
        name = (serialized or {}).get("name") or kw.get("name") or "Other"
        inp = inputs if isinstance(inputs, dict) else {"input": input_str}
        self._tools[run_id] = (time.perf_counter(), name, inp)
        self.t.tool_call(name, inp, tool_use_id=str(run_id) if run_id else None)

    def on_tool_end(self, output, *, run_id=None, **kw):
        t0, name, inp = self._tools.pop(run_id, (None, kw.get("name") or "Other", {}))
        self.t.tool_result(name, inp, output=output, duration_ms=(time.perf_counter() - t0) * 1000 if t0 else None,
                           tool_use_id=str(run_id) if run_id else None)

    def on_tool_error(self, error, *, run_id=None, **kw):
        t0, name, inp = self._tools.pop(run_id, (None, kw.get("name") or "Other", {}))
        if isinstance(error, DinoGuardDenied):
            return
        self.t.tool_result(name, inp, error=f"{type(error).__name__}: {error}",
                           duration_ms=(time.perf_counter() - t0) * 1000 if t0 else None,
                           tool_use_id=str(run_id) if run_id else None)

    def on_chain_error(self, error, **kw):
        if not isinstance(error, DinoGuardDenied):
            self.t.error(error, where="chain")

    def __getattr__(self, name):       # every other callback (on_text, on_retry, ...) is a no-op
        if name.startswith("on_"):
            return lambda *a, **k: None
        raise AttributeError(name)


def langchain_handler(tracer: Tracer, **kw):
    """A real BaseCallbackHandler subclass when langchain_core is installed, else the duck-typed handler."""
    try:
        from langchain_core.callbacks import BaseCallbackHandler  # type: ignore
    except ImportError:
        return LangChainCallbacks(tracer, **kw)
    cls = type("DinoGuardCallbackHandler", (LangChainCallbacks, BaseCallbackHandler), {})
    return cls(tracer, **kw)


def _traced(fn, tool_name: str, tracer: Tracer):
    if getattr(fn, "__dinoguard__", False):
        return fn

    def bound_input(args, kwargs) -> dict:
        try:
            b = inspect.signature(fn).bind_partial(*args, **kwargs)
            return {k: v for k, v in b.arguments.items() if k not in ("self", "ctx", "run_manager")}
        except (TypeError, ValueError):
            return {"args": list(args), **kwargs}

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def awrapper(*args, **kwargs):
            inp = bound_input(args, kwargs)
            tracer.tool_call(tool_name, inp)
            t0 = time.perf_counter()
            try:
                res = await fn(*args, **kwargs)
            except Exception as e:
                tracer.tool_result(tool_name, inp, error=f"{type(e).__name__}: {e}",
                                   duration_ms=(time.perf_counter() - t0) * 1000)
                raise
            tracer.tool_result(tool_name, inp, output=res, duration_ms=(time.perf_counter() - t0) * 1000)
            return res
        awrapper.__dinoguard__ = True
        return awrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        inp = bound_input(args, kwargs)
        return tracer.run_tool(tool_name, inp, lambda: fn(*args, **kwargs))
    wrapper.__dinoguard__ = True
    return wrapper


def traced_tools(tools: list, tracer: Tracer, attrs=("func", "_run", "fn", "async_fn", "coroutine", "run")) -> list:
    """Wrap the callable inside tool objects (LangChain Tool.func, CrewAI BaseTool._run, LlamaIndex
    FunctionTool.fn ...) so every call is checked and recorded. Modifies the objects in place."""
    for t in tools:
        name = getattr(t, "name", None) or getattr(getattr(t, "metadata", None), "name", None) or type(t).__name__
        for attr in attrs:
            f = getattr(t, attr, None)
            if callable(f):
                g = _traced(f, str(name), tracer)
                try:
                    setattr(t, attr, g)
                except (AttributeError, TypeError, ValueError):
                    object.__setattr__(t, attr, g)
                break
    return tools


def pydantic_ai_tool(tracer: Tracer, name: str | None = None):
    """Decorator for Pydantic AI tool functions (put it under @agent.tool). functools.wraps keeps the signature,
    so Pydantic AI still builds the same schema."""
    def deco(fn):
        return _traced(fn, name or fn.__name__, tracer)
    return deco


def guard_tool_request(tracer: Tracer, request, handler, make_blocked):
    """Core of the Deep Agents / LangChain `wrap_tool_call` middleware, kept framework-free for testing.
    request.tool_call = {"name", "args", "id"}; make_blocked(text, call) builds the ToolMessage to return."""
    call = getattr(request, "tool_call", None) or {}
    name, args = str(call.get("name") or "Other"), dict(call.get("args") or {})
    d = tracer.event("PreToolUse", name, args, tool_use_id=call.get("id"))
    if d.kind == "deny":
        return make_blocked(f"Blocked by DinoGuard: {d.reason}", call)
    t0 = time.perf_counter()
    try:
        result = handler(request)
    except Exception as e:
        tracer.tool_result(name, args, error=f"{type(e).__name__}: {e}", duration_ms=(time.perf_counter() - t0) * 1000,
                           tool_use_id=call.get("id"))
        raise
    content = getattr(result, "content", result)
    tracer.tool_result(name, args, output=content, duration_ms=(time.perf_counter() - t0) * 1000,
                       tool_use_id=call.get("id"))
    return result


def deepagents_middleware(tracer: Tracer):
    """Middleware for `create_deep_agent(..., middleware=[deepagents_middleware(t)])` (and LangChain v1 agents).
    A denied call never runs: the model gets an error ToolMessage with DinoGuard's reason instead."""
    from langchain.agents.middleware import wrap_tool_call  # type: ignore
    from langchain_core.messages import ToolMessage  # type: ignore

    def blocked(text, call):
        return ToolMessage(content=text, tool_call_id=call.get("id") or "", name=call.get("name"), status="error")

    @wrap_tool_call
    def dinoguard_guard(request, handler):
        return guard_tool_request(tracer, request, handler, blocked)
    return dinoguard_guard


def crewai_step_callback(tracer: Tracer):
    """CrewAI `step_callback=`: records each agent step's tool use (observe; use traced_tools to block)."""
    def cb(step):
        tool = getattr(step, "tool", None)
        if tool:
            tracer.tool_result(str(tool), {"input": str(getattr(step, "tool_input", ""))[:2000]},
                               output=str(getattr(step, "result", ""))[:4000])
    return cb
