"""Evaluate one canonical event against the registry.

Rules (see CLAUDE.md §5):
- built-ins first, self-protect first of all
- first enforced deny stops evaluation; enforced instructs are merged
- each policy gets a 10 s deadline; errors/timeouts => allow (logged), except self-protect => deny
- observe-mode policies are recorded ("would deny") but never enforced
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..util import now_iso, parse_iso
from .context import History, PolicyContext, TokenStats
from .decisions import ALLOW, DENY, INSTRUCT, Decision, merge
from .events import CanonicalEvent
from .modes import ENFORCE, OBSERVE, effective
from .registry import SELF_PROTECT, Policy, Registry

POLICY_DEADLINE_S = 10.0
PACK_UNAVAILABLE = "pack/pack-unavailable"


@dataclass
class PolicyResult:
    policy: str
    decision: Decision
    mode: str
    authority: str
    latency_ms: float
    version: int | None = None
    error: str | None = None
    reviewer_verdict: dict[str, Any] | None = None
    enforced: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"policy": self.policy, "decision": self.decision.kind, "reason": self.decision.reason,
                "mode": self.mode, "authority": self.authority, "latency_ms": round(self.latency_ms, 2),
                "version": self.version, "error": self.error, "reviewer_verdict": self.reviewer_verdict,
                "enforced": self.enforced}


@dataclass
class Evaluation:
    event: CanonicalEvent
    decision: Decision
    results: list[PolicyResult] = field(default_factory=list)

    @property
    def observed(self) -> list[PolicyResult]:
        return [r for r in self.results if r.mode == OBSERVE and r.decision.kind != ALLOW]


class _Timeout(Exception):
    pass


def call_with_deadline(fn: Callable, ctx: PolicyContext, deadline: float):
    box: dict[str, Any] = {}

    def target():
        try:
            box["value"] = fn(ctx)
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=target, daemon=True, name=f"policy:{ctx.policy_name}")
    t.start()
    t.join(deadline)
    if t.is_alive():
        raise _Timeout(f"policy exceeded {deadline:.0f}s deadline")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _coerce(value) -> Decision:
    if value is None:
        return Decision(ALLOW)
    if isinstance(value, Decision):
        return value
    raise TypeError(f"policy returned {type(value).__name__}, expected allow()/instruct()/deny()")


def is_paused(cfg: dict) -> bool:
    until = cfg.get("pause_until")
    if not until:
        return False
    try:
        return parse_iso(until) > parse_iso(now_iso())
    except ValueError:
        return False


def _pack_unavailable(event: CanonicalEvent, registry: Registry, cfg: dict) -> Decision | None:
    if is_paused(cfg):
        return None
    for miss in registry.missing:
        if miss.get("mode") != ENFORCE or miss.get("transient"):
            continue
        over = (cfg.get("policies") or {}).get(miss["name"], {})
        if over.get("enabled") is False:
            continue
        events, tools = miss.get("events"), miss.get("tools")
        if events and event.event not in events:
            continue
        if tools and event.tool_name not in tools:
            continue
        msg = (f"Policy pack '{miss.get('pack')}' is enforced but policy '{miss['name']}' failed to load "
               f"({miss.get('error', 'unavailable')}). Actions it covers are blocked until the operator "
               f"runs `dinoguard doctor` and repairs the pack.")
        if event.event == "UserPromptSubmit":
            return Decision(INSTRUCT, msg, policy=PACK_UNAVAILABLE)
        return Decision(DENY, msg, policy=PACK_UNAVAILABLE)
    return None


def evaluate(event: CanonicalEvent, registry: Registry, cfg: dict | None = None,
             history: History | None = None, tokens: TokenStats | None = None, probe=None,
             reviewer=None, approver=None, audit: bool = False,
             deadline_s: float = POLICY_DEADLINE_S) -> Evaluation:
    cfg = cfg or {}
    history = history or History()
    tokens = tokens or TokenStats()
    if probe is None:
        from ..policies.builtin._probe import Probe
        probe = Probe()
    results: list[PolicyResult] = []

    if not audit:
        pack_d = _pack_unavailable(event, registry, cfg)
        if pack_d is not None and pack_d.kind == DENY:
            results.append(PolicyResult(PACK_UNAVAILABLE, pack_d, ENFORCE, "hard", 0.0, enforced=True))
            return Evaluation(event, pack_d, results)
        if pack_d is not None:
            results.append(PolicyResult(PACK_UNAVAILABLE, pack_d, ENFORCE, "hard", 0.0, enforced=True))

    enforced: list[Decision] = [r.decision for r in results]
    final: Decision | None = None

    for p in registry.policies:
        eff = effective(p, cfg)
        if not audit and not eff.enabled:
            continue
        if not p.matches(event.event, event.tool_name):
            continue
        mode = OBSERVE if audit else eff.mode
        ctx = PolicyContext(event=event, params=eff.params, history=history, tokens=tokens,
                            probe=probe, policy_name=p.name)
        t0 = time.perf_counter()
        err = None
        try:
            # audits replay offline history: call directly (no per-call thread) for speed
            d = _coerce(p.fn(ctx) if audit else call_with_deadline(p.fn, ctx, deadline_s))
        except BaseException as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            if p.name == SELF_PROTECT:
                d = Decision(DENY, "DinoGuard self-protection could not verify this action, so it is "
                                   "blocked. Ask the user to run `dinoguard doctor`.")
            else:
                d = Decision(ALLOW)
        d.policy = d.policy or p.name
        r = PolicyResult(p.name, d, mode, p.authority, (time.perf_counter() - t0) * 1000,
                         version=p.version, error=err)
        results.append(r)

        if d.kind != ALLOW and p.authority == "reviewable" and reviewer is not None and not audit:
            verdict = reviewer.clear(ctx, p, d)
            r.reviewer_verdict = verdict
            if verdict and verdict.get("cleared") and verdict.get("mode") == ENFORCE:
                d = Decision(ALLOW, f"cleared by reviewer: {verdict.get('summary', '')}", policy=p.name)
                r.decision = d

        if mode != ENFORCE or d.kind == ALLOW:
            continue
        r.enforced = True
        if d.kind == DENY:
            if d.hold and approver is not None:
                d = approver(event, d)
                r.decision = d
                if d.kind != DENY:
                    enforced.append(d)
                    continue
            final = d
            break
        enforced.append(d)

    if final is None and reviewer is not None and not audit and event.event in ("PreToolUse", "PermissionRequest"):
        extra = reviewer.independent(PolicyContext(event=event, history=history, tokens=tokens, probe=probe))
        for res in extra or []:
            results.append(res)
            if res.enforced and res.decision.kind == DENY:
                final = res.decision
                break
            if res.enforced and res.decision.kind == INSTRUCT:
                enforced.append(res.decision)

    if final is None:
        final = merge(enforced)
    return Evaluation(event, final, results)
