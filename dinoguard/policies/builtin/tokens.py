"""Token coaching: instruct-only tips (budget is the one that can deny)."""
from __future__ import annotations

from ...engine.decisions import allow, deny, instruct
from ...engine.registry import builtin_policy
from ...util import path_key
from ._common import file_path

_TIP = dict(category="tokens", default_enabled=False)


@builtin_policy("token-repeat-read", events=["PreToolUse"], params={"times": 3}, **_TIP,
                description="Tip when the same file is read 3+ times in a session.")
def token_repeat_read(ctx):
    if ctx.tool_name != "Read":
        return allow()
    key = path_key(file_path(ctx), ctx.cwd)
    n = 1 + sum(1 for h in ctx.history.tool_calls(0)
                if h.get("tool_name") == "Read" and path_key(str((h.get("input") or {}).get("file_path", "")), ctx.cwd) == key)
    if n >= int(ctx.params.get("times", 3)) and not ctx.history.policy_fired_recently("token-repeat-read", 10):
        return instruct(f"Token tip: this is read #{n} of {file_path(ctx)} this session. Keep the parts you need in "
                        "mind, or read a specific range (offset/limit) instead of the whole file again.")
    return allow()


@builtin_policy("token-big-output", events=["PostToolUse"], params={"max_tokens": 20000}, **_TIP,
                description="Tip when a tool output is larger than ~20k tokens.")
def token_big_output(ctx):
    approx = (ctx.event.output_chars or len(ctx.event.output or "")) // 4
    if approx > int(ctx.params.get("max_tokens", 20000)):
        return instruct(f"Token tip: that output was ~{approx // 1000}k tokens. Next time search for what you need "
                        "(grep/rg with a pattern, head/tail, a line range) instead of dumping everything.")
    return allow()


@builtin_policy("token-context-high", params={"pct": 0.8}, events=["PreToolUse", "UserPromptSubmit"], **_TIP,
                description="Tip to compact when the context window is 80%+ full.")
def token_context_high(ctx):
    if ctx.event_type not in ("PreToolUse", "UserPromptSubmit"):
        return allow()
    pct = ctx.tokens.context_pct
    if pct >= float(ctx.params.get("pct", 0.8)) and not ctx.history.policy_fired_recently("token-context-high", 25):
        return instruct(f"Token tip: the context window is {pct:.0%} full. Wrap up the current step and suggest the "
                        "user runs /compact (or summarise and start a fresh session).")
    return allow()


@builtin_policy("token-model-fit", params={"large_models": ["opus"], "turns": 10, "small_chars": 200}, **_TIP,
                description="Tip when many tiny edits run on the largest model.")
def token_model_fit(ctx):
    if ctx.event_type != "PreToolUse" or ctx.tool_name not in ("Edit", "Write"):
        return allow()
    model = (ctx.tokens.model or "").lower()
    if not any(m in model for m in ctx.params.get("large_models", ["opus"])):
        return allow()
    n, small = int(ctx.params.get("turns", 10)), int(ctx.params.get("small_chars", 200))
    edits = [h for h in ctx.history.tool_calls(0) if h.get("tool_name") in ("Edit", "Write")][-n:]
    if len(edits) < n:
        return allow()
    sizes = [len(str((h.get("input") or {}).get("new_string") or (h.get("input") or {}).get("content") or "")) for h in edits]
    if all(s <= small for s in sizes) and not ctx.history.policy_fired_recently("token-model-fit", 30):
        return instruct(f"Token tip: the last {n} edits were all tiny, on {ctx.tokens.model}. Suggest the user switch "
                        "to a smaller, cheaper model for this kind of mechanical work.")
    return allow()


@builtin_policy("token-budget", params={"budget_usd": None, "warn_pct": 0.8},
                events=["PreToolUse", "UserPromptSubmit"], **_TIP,
                description="Instruct at 80% of the session budget; deny at 100% until the user raises it.")
def token_budget(ctx):
    budget = ctx.params.get("budget_usd")
    if not budget or ctx.event_type not in ("PreToolUse", "UserPromptSubmit"):
        return allow()
    budget = float(budget)
    cost = ctx.tokens.cost_usd
    if cost >= budget:
        return deny(f"The session budget of ${budget:.2f} is used up (${cost:.2f} spent). Stop and ask the user to "
                    "approve more budget: `dinoguard policies set token-budget budget_usd=<new>`.")
    if cost >= budget * float(ctx.params.get("warn_pct", 0.8)) and not ctx.history.policy_fired_recently("token-budget", 20):
        return instruct(f"Budget tip: ${cost:.2f} of ${budget:.2f} spent ({cost / budget:.0%}). Prioritise finishing "
                        "the core task and avoid exploratory reads.")
    return allow()
