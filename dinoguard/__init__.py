"""DinoGuard public SDK.

    from dinoguard import policy, allow, instruct, deny, check
"""
from .engine.decisions import Decision, allow, ask_human, deny, instruct
from .engine.registry import policy
from .version import PROTOCOL_VERSION, __version__


def check(tool: str, input: dict | None = None, session_id: str | None = None, **kw) -> Decision:  # noqa: A002
    """Evaluate a tool call from a custom agent. See dinoguard.adapters.sdk.check."""
    from .adapters.sdk import check as _check
    return _check(tool, input or {}, session_id=session_id, **kw)


__all__ = ["policy", "allow", "instruct", "deny", "ask_human", "check", "Decision", "__version__",
           "PROTOCOL_VERSION"]
