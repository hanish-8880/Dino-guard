"""Map stream records (decisions / notes) to a runner state."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

STATES = ["walk", "sprint", "instruct", "deny", "shield", "review", "wait", "ghost", "guard_down"]
STYLE = {
    "walk": "green", "sprint": "dark_orange", "instruct": "yellow", "deny": "red", "shield": "magenta",
    "review": "medium_purple", "wait": "dodger_blue1", "ghost": "grey50", "guard_down": "white on red",
}
TRANSIENT = {"instruct": 3.0, "deny": 4.0, "shield": 4.0, "ghost": 2.5, "sprint": 3.0}


@dataclass
class RunnerState:
    burn_high_per_min: float = 20000.0
    state: str = "walk"
    since: float = field(default_factory=time.monotonic)
    message: str = ""
    tip: str = ""
    last: str = ""
    tokens: dict = field(default_factory=dict)
    events: int = 0

    def _set(self, st: str, now: float):
        self.state, self.since = st, now

    def feed(self, rec: dict, now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        kind = rec.get("kind", "event")
        if rec.get("tokens"):
            self.tokens = rec["tokens"]
        if kind == "guard_down":
            self._set("guard_down", now)
            self.message = f"GUARD DOWN: {rec.get('reason', '')}"
            return self.state
        if kind == "approval_wait":
            self._set("wait", now)
            self.message = f"waiting for phone approval #{rec.get('code')}: {rec.get('summary', '')}"
            return self.state
        if kind == "approval_done":
            self._set("walk" if rec.get("answer") == "yes" else "deny", now)
            self.message = f"approval #{rec.get('code')}: {rec.get('answer')}"
            return self.state
        if kind == "review":
            self._set("review", now)
            self.message = f"reviewer checking: {rec.get('summary', '')}"
            return self.state
        self.events += 1
        d, pol = rec.get("decision"), rec.get("policy") or ""
        self.last = f"{rec.get('tool') or rec.get('event', '')} {rec.get('summary', '')} {d}".strip()
        if d == "deny":
            self._set("shield" if pol == "guard-self-protect" else "deny", now)
            self.message = f"[{pol}] {rec.get('reason') or ''}"
        elif d == "instruct":
            if pol.startswith("token-"):
                self._set("sprint", now)
                self.tip = rec.get("reason") or ""
            else:
                self._set("instruct", now)
            self.message = f"[{pol}] {rec.get('reason') or ''}"
        elif any(o.get("decision") in ("deny", "instruct") for o in rec.get("observed") or []):
            self._set("ghost", now)
            o = rec["observed"][0]
            self.message = f"observe: {o['policy']} would {o['decision']}"
        else:
            self._set("sprint" if self.burn_high() else "walk", now)
            if self.state == "walk":
                self.message = ""
        return self.state

    def burn_high(self) -> bool:
        return float(self.tokens.get("burn_per_min") or 0) >= self.burn_high_per_min

    def current(self, now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        hold = TRANSIENT.get(self.state)
        if hold and now - self.since > hold:
            self._set("sprint" if self.burn_high() else "walk", now)
            if self.state == "walk":
                self.message, self.tip = "", ""
        return self.state
