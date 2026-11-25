"""allow / instruct / deny and how several results combine."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

ALLOW, INSTRUCT, DENY = "allow", "instruct", "deny"
KINDS = (ALLOW, INSTRUCT, DENY)


@dataclass
class Decision:
    kind: str = ALLOW
    reason: str | None = None
    policy: str | None = None
    # "hold" = deny unless a human approves (phone approval). Resolved by the runner.
    hold: bool = False
    hold_timeout_s: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"invalid decision kind {self.kind!r}")

    @property
    def is_deny(self) -> bool:
        return self.kind == DENY

    @property
    def is_instruct(self) -> bool:
        return self.kind == INSTRUCT

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Decision":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


def allow(reason: str | None = None) -> Decision:
    return Decision(ALLOW, reason)


def instruct(reason: str) -> Decision:
    if not reason:
        raise ValueError("instruct() needs a reason written for the agent")
    return Decision(INSTRUCT, reason)


def deny(reason: str) -> Decision:
    if not reason:
        raise ValueError("deny() needs a reason written for the agent")
    return Decision(DENY, reason)


def ask_human(reason: str, timeout_s: int = 120) -> Decision:
    """Hold the action until a human answers on their phone. No answer = deny."""
    return Decision(DENY, reason, hold=True, hold_timeout_s=timeout_s)


def merge(decisions: Iterable[Decision]) -> Decision:
    """First deny wins. Otherwise all instruct reasons are combined. Otherwise allow."""
    instructs: list[Decision] = []
    for d in decisions:
        if d.kind == DENY:
            return d
        if d.kind == INSTRUCT:
            instructs.append(d)
    if not instructs:
        return Decision(ALLOW)
    if len(instructs) == 1:
        return instructs[0]
    text = "\n".join(f"- [{d.policy}] {d.reason}" if d.policy else f"- {d.reason}" for d in instructs)
    return Decision(INSTRUCT, text, policy=",".join(d.policy or "?" for d in instructs))
