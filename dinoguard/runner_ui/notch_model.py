"""Display-free logic for the floating notch: placement, colours, expand timing, saved position.

Kept separate from the tkinter window so it can be unit-tested without a screen.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field

from .. import paths
from .state import RunnerState

COLLAPSED = (230, 46)
EXPANDED = (330, 104)
EXPAND_S = 4.0
TRANSITION_S = 0.35
MARGIN = 12
CORNERS = ("bottom-left", "bottom-right", "top-center")
DEFAULT_CORNER = "bottom-left"

BG = "#1F1F1E"
FG = "#ECEBE6"
MUTED = "#8A8984"
TRACK = "#3A3A37"
DINO_GREY = "#9A9994"
DINO_DIM = "#4E4E4A"
KEY = "#ff00fe"          # transparent-colour key (never drawn on purpose)

COLOR = {                # status text / wall colour per state
    "walk": "#97C459", "sprint": "#EF9F27", "instruct": "#E8C547", "deny": "#E24B4A",
    "shield": "#7F77DD", "review": "#A89FEA", "wait": "#85B7EB", "ghost": "#8A8984",
    "guard_down": "#ffffff",
}
BLINK_GREY = {"deny", "shield"}      # dino turns grey and blinks behind the wall
LABEL = {
    "walk": "allow", "sprint": "tip", "instruct": "instruct", "deny": "deny", "shield": "shield",
    "review": "review", "wait": "approve?", "ghost": "observe", "guard_down": "GUARD DOWN",
}
EXPAND_ON = {"deny", "shield", "instruct", "sprint", "wait"}


# ---- screen geometry -------------------------------------------------------------------------

def work_area(screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the usable desktop, i.e. excluding the taskbar."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            r = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0):   # SPI_GETWORKAREA
                if r.right > r.left and r.bottom > r.top:
                    return r.left, r.top, r.right, r.bottom
        except (OSError, AttributeError, ValueError):
            pass
    return 0, 0, screen_w, max(1, screen_h - 48)   # guess a taskbar-sized strip at the bottom


def corner_xy(corner: str, area: tuple[int, int, int, int], size=COLLAPSED) -> tuple[int, int]:
    left, top, right, bottom = area
    w, h = size
    if corner == "bottom-right":
        return right - w - MARGIN, bottom - h - MARGIN
    if corner == "top-center":
        return left + (right - left - w) // 2, top + MARGIN
    return left + MARGIN, bottom - h - MARGIN


def clamp(x: int, y: int, size, area) -> tuple[int, int]:
    left, top, right, bottom = area
    w, h = size
    x = max(left, min(x, right - w))
    y = max(top, min(y, bottom - h))
    return x, y


def grow(x: int, y: int, small, big, area) -> tuple[int, int]:
    """Where the expanded pill goes so it grows away from the nearest screen edges."""
    left, top, right, bottom = area
    sw, sh = small
    bw, bh = big
    cx, cy = x + sw / 2, y + sh / 2
    mid_x, mid_y = (left + right) / 2, (top + bottom) / 2
    if abs(cx - mid_x) < (right - left) * 0.15:
        nx = int(cx - bw / 2)                 # centred: grow both ways
    elif cx > mid_x:
        nx = x + sw - bw                      # right side: keep the right edge
    else:
        nx = x
    ny = y + sh - bh if cy > mid_y else y     # bottom half: grow upwards
    return clamp(nx, ny, big, area)


# ---- saved position --------------------------------------------------------------------------

def pos_path():
    return paths.home() / "notch.json"


def load_pos() -> dict:
    try:
        data = json.loads(pos_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            if data.get("corner") not in CORNERS:
                data["corner"] = DEFAULT_CORNER
            return data
    except (OSError, ValueError):
        pass
    return {"corner": DEFAULT_CORNER}


def save_pos(data: dict) -> None:
    p = pos_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def start_xy(pos: dict, area) -> tuple[int, int]:
    """Saved drag position if it still fits on this screen, else the saved corner."""
    if isinstance(pos.get("x"), int) and isinstance(pos.get("y"), int):
        return clamp(pos["x"], pos["y"], COLLAPSED, area)
    return corner_xy(pos.get("corner", DEFAULT_CORNER), area)


# ---- text helpers ----------------------------------------------------------------------------

def short_num(n) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n // 1000}k"
    return str(n)


def fuel(tokens: dict) -> tuple[float, str]:
    """(fraction of context window used, caption)."""
    window = int(tokens.get("context_window") or 200_000)
    used = int(tokens.get("context_tokens") or 0)
    frac = max(0.0, min(1.0, used / window)) if window else 0.0
    cost = float(tokens.get("cost_usd") or 0)
    return frac, f"{short_num(used)}/{short_num(window)} · ${cost:.2f}"


def fuel_color(frac: float) -> str:
    return COLOR["deny"] if frac >= 0.8 else COLOR["sprint"] if frac >= 0.6 else COLOR["walk"]


def ease(t: float) -> float:
    """Ease-in-out (cubic) for the 0.35 s grow/shrink."""
    t = max(0.0, min(1.0, t))
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def lerp_rect(a, b, k: float) -> tuple[int, int, int, int]:
    return tuple(int(round(x + (y - x) * k)) for x, y in zip(a, b))


def ellipsize(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: max(0, n - 1)] + "…"


# ---- live model ------------------------------------------------------------------------------

@dataclass
class NotchModel:
    """RunnerState plus the bits the pill needs: last action and when to expand."""
    runner: RunnerState = field(default_factory=RunnerState)
    action: str = ""
    expand_until: float = 0.0

    def feed(self, rec: dict, now: float | None = None, quiet: bool = False) -> str:
        now = time.monotonic() if now is None else now
        st = self.runner.feed(rec, now)
        if rec.get("kind", "event") == "event":
            tool = rec.get("tool") or rec.get("event") or ""
            self.action = ellipsize(f"{tool} {rec.get('summary') or ''}", 60)
        if not quiet and st in EXPAND_ON:
            self.expand_until = now + EXPAND_S
        return st

    def state(self, now: float | None = None) -> str:
        return self.runner.current(now)

    def expanded(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return now < self.expand_until

    def status(self, now: float | None = None) -> str:
        st = self.state(now)
        if st == "guard_down":
            return "GUARD DOWN · run dinoguard doctor"
        if st == "wait":
            return "approve? · check your phone"
        return f"{LABEL[st]} · {self.action}" if self.action else LABEL[st]

    def detail(self) -> str:
        r = self.runner
        return r.tip if r.state == "sprint" and r.tip else r.message
