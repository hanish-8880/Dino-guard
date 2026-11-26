"""Agent contract (CLAUDE.md §17.4): `should_not` rules from the project's contract.yaml, checked live.

Off by default and observe-only by default: it records "would deny" so you can see contract breaks first.
"""
from __future__ import annotations

from ...audit import contract as contract_mod
from ...engine.decisions import allow, deny
from ...engine.registry import builtin_policy


@builtin_policy("contract-should-not", description="Flag tool calls that break the project's agent contract.",
                category="workflow", default_enabled=False, default_mode="observe", params={"contract": None})
def contract_should_not(ctx):
    inline = ctx.params.get("contract")
    c = contract_mod.parse(inline) if isinstance(inline, dict) else contract_mod.load(ctx.cwd)
    broken = contract_mod.violations(c, ctx.tool_name, ctx.tool_input)
    if not broken:
        return allow()
    texts = "; ".join(r["text"] for r in broken[:3])
    return deny(f"The project's agent contract says the agent should not: {texts}. Leave this out of the task, "
                "or ask the user before doing it.")
