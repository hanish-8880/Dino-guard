"""Render docs/runner.gif from the real runner scene code (dev-only; needs Pillow, not a runtime dependency).

    python demo/make_gif.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from dinoguard.runner_ui.scene import render  # noqa: E402

CW, CH = 10, 20          # cell size in px
COLS = 72
RGB = {"green": (12, 163, 12), "green4": (0, 120, 0), "dark_orange": (236, 131, 90), "yellow": (250, 178, 25),
       "red": (208, 59, 59), "magenta": (176, 92, 214), "medium_purple": (144, 133, 233), "dodger_blue1": (57, 135, 229),
       "grey50": (127, 127, 127), "grey42": (90, 90, 86), "grey62": (158, 158, 158), "white": (230, 230, 225)}
BITS = [(0, 0, 0x01), (0, 1, 0x02), (0, 2, 0x04), (1, 0, 0x08), (1, 1, 0x10), (1, 2, 0x20), (0, 3, 0x40), (1, 3, 0x80)]
SCRIPT = [
    ("walk", "", 14), ("instruct", "[warn-destructive-sql] DROP TABLE - confirm it is a dev database first", 14),
    ("deny", "[no-rm-rf] Recursive force delete is blocked. Move the directory aside instead.", 16),
    ("shield", "[guard-self-protect] Disabling DinoGuard is never allowed for the agent.", 14),
    ("sprint", "", 14), ("ghost", "observe: no-chmod-777 would deny", 14),
    ("review", "reviewer checking: kubectl apply -f prod.yaml", 12),
    ("wait", "waiting for phone approval #4821: terraform apply", 14),
    ("guard_down", "GUARD DOWN: daemon unreachable - every action is denied", 14), ("walk", "", 10),
]


def color(style: str) -> tuple[tuple, tuple | None]:
    parts = (style or "").replace("bold ", "").split(" on ")
    fg = RGB.get(parts[0].strip(), (230, 230, 225))
    bg = (110, 20, 20) if len(parts) > 1 else None
    return fg, bg


def draw(canvas, font) -> Image.Image:
    img = Image.new("RGB", (COLS * CW, canvas.h * CH), (0, 0, 0))
    d = ImageDraw.Draw(img)
    for y, row in enumerate(canvas.cells):
        for x, (ch, st) in enumerate(row):
            fg, bg = color(st)
            if bg:
                d.rectangle([x * CW, y * CH, (x + 1) * CW - 1, (y + 1) * CH - 1], fill=bg)
            o = ord(ch)
            if 0x2800 <= o <= 0x28FF:
                for dx, dy, bit in BITS:
                    if (o - 0x2800) & bit:
                        cx, cy = x * CW + 3 + dx * 4, y * CH + 3 + dy * 4.5
                        d.ellipse([cx - 1.6, cy - 1.6, cx + 1.6, cy + 1.6], fill=fg)
            elif ch.strip():
                d.text((x * CW, y * CH + 2), {"▕": "[", "▏": "]"}.get(ch, ch), fill=fg, font=font)
    return img


def main() -> None:
    try:
        font = ImageFont.truetype("consola.ttf", 15)
    except OSError:
        font = ImageFont.load_default()
    tok = {"context_tokens": 42000, "context_window": 200000, "burn_per_min": 900, "cost_usd": 0.31}
    frames, tick = [], 0
    for state, msg, n in SCRIPT:
        t = dict(tok, burn_per_min=45000, context_tokens=150000, cost_usd=1.84) if state == "sprint" else tok
        for _ in range(n):
            c = render(state, COLS, tick, msg, "grep for what you need instead of cat", t, "Bash npm test allow")
            frames.append(draw(c, font))
            tick += 1
    out = ROOT / "docs" / "runner.gif"
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=110, loop=0, optimize=True)
    print(f"wrote {out} ({len(frames)} frames)")


if __name__ == "__main__":
    main()
