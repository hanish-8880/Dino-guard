"""Render one runner frame into a styled character grid (pure; no terminal I/O)."""
from __future__ import annotations

from rich.text import Text

from . import art
from .state import STYLE

HEIGHT = 8


class Canvas:
    def __init__(self, width: int, height: int = HEIGHT, bg: str = ""):
        self.w, self.h = max(10, width), height
        self.cells = [[(" ", bg) for _ in range(self.w)] for _ in range(height)]
        self.bg = bg

    def put(self, x: int, y: int, text: str, style: str = ""):
        if not 0 <= y < self.h:
            return
        for i, ch in enumerate(text):
            xx = x + i
            if 0 <= xx < self.w:
                self.cells[y][xx] = (ch, style or self.bg)

    def plain(self) -> list[str]:
        return ["".join(c for c, _ in row) for row in self.cells]

    def to_text(self) -> Text:
        t = Text()
        for yi, row in enumerate(self.cells):
            run, style = "", None
            for ch, st in row:
                if st != style and run:
                    t.append(run, style or None)
                    run = ""
                style = st
                run += ch
            if run:
                t.append(run, style or None)
            if yi < self.h - 1:
                t.append("\n")
        return t


def _fmt_k(n) -> str:
    n = int(n or 0)
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def token_line(tokens: dict, last: str, width: int, burn_high: float) -> str:
    ctx, win = tokens.get("context_tokens", 0), tokens.get("context_window", 200000) or 200000
    pct = min(1.0, ctx / win) if win else 0
    bar_w = 8 if width >= 60 else 4
    filled = round(pct * bar_w)
    bar = "▕" + "█" * filled + "░" * (bar_w - filled) + "▏"
    burn = float(tokens.get("burn_per_min") or 0)
    level = "high" if burn >= burn_high else "med" if burn >= burn_high / 3 else "low"
    s = f"tokens {_fmt_k(ctx)}/{_fmt_k(win)} {bar} burn: {level} · ${float(tokens.get('cost_usd') or 0):.2f}"
    if last:
        s += f" · last: {last}"
    return s[:width]


def render(state: str, width: int, tick: int, message: str = "", tip: str = "", tokens: dict | None = None,
           last: str = "", burn_high: float = 20000.0) -> Canvas:
    style = STYLE.get(state, "green")
    bg = "on dark_red" if state == "guard_down" else ""
    c = Canvas(width, HEIGHT, bg)
    w = c.w
    title = f" DinoGuard · {state.replace('_', ' ')} "
    c.put(0, 0, title[: w], f"bold {style}" if not bg else "bold white on dark_red")

    moving = state in ("walk", "sprint", "ghost", "review", "instruct")
    speed = 2 if state == "sprint" else 1 if moving else 0
    if state == "instruct":
        speed = 1 if tick % 2 == 0 else 0     # slows down at a dashed wall
    offset = (tick * speed)
    dino_x = 3 if w >= 40 else 1
    frame = "stop"
    if moving:
        frame = "a" if (tick // (2 if state == "sprint" else 3)) % 2 == 0 else "b"
    if state in ("deny", "shield") and (tick // 5) % 2 == 1:
        frame = "blink"
    sprite = art.SPRITES[frame]
    dino_style = "grey62" if state == "guard_down" else ("green" if state in ("walk", "ghost") else style)
    if state == "guard_down":
        dino_style = "white on dark_red"
    for i, row in enumerate(sprite):
        c.put(dino_x, 2 + i, row, dino_style)

    if state == "sprint":
        for i in range(3):
            c.put(max(0, dino_x - 3), 2 + i, art.SPEED * 2, "dark_orange")
        if tip and w >= 30:
            c.put(dino_x + art.SPRITE_W + 2, 1, ("tip: " + tip)[: w - dino_x - art.SPRITE_W - 3], "dark_orange")
    if state == "wait" and (tick // 5) % 2 == 0:
        c.put(dino_x + 5, 1, "?", "bold dodger_blue1")
    if state == "review":
        pos = (tick % max(1, w - 2))
        c.put(pos, 1, "• • •", "medium_purple")

    wall_x = dino_x + art.SPRITE_W + 2
    wall_rows = range(1, 5)
    if state == "deny":
        for y in wall_rows:
            c.put(wall_x, y, art.WALL_SOLID * 2, "bold red")
    elif state == "shield":
        for y in wall_rows:
            c.put(wall_x, y, art.WALL_SOLID * 2, "bold magenta")
    elif state == "instruct":
        for y in wall_rows:
            if y % 2 == 1:
                c.put(wall_x + 1, y, art.WALL_DASH, "bold yellow")
    elif state == "ghost":
        gx = (w - 2) - (tick % max(1, w))   # wall scrolls toward and through the dino
        for y in wall_rows:
            c.put(gx, y, art.WALL_GHOST, "grey50")

    g = art.GROUND
    ground = "".join(g[(i + offset) % len(g)] for i in range(w))
    c.put(0, 5, ground, "red" if state == "guard_down" else "grey42" if state != "walk" else "green4")

    if message:
        msg_style = {"deny": "red", "shield": "magenta", "instruct": "yellow", "ghost": "grey62",
                     "wait": "dodger_blue1", "review": "medium_purple"}.get(state, "white")
        c.put(0, 6, message[: w], msg_style if not bg else "bold white on dark_red")
    c.put(0, 7, token_line(tokens or {}, last, w, burn_high), "white" if not bg else "white on dark_red")
    return c
