"""Original dotted dino ("Dot", a little plated guard-dino) drawn on a pixel grid, rendered as braille."""
from __future__ import annotations

# 22 x 15 dot frames: 13 body rows + 2 leg rows. 'X' = dot on. Head on the right, back spikes on top,
# tail on the left. Walking alternates legs A / B.
_BODY = [
    "..............XXXXXX..",
    ".............XXXXXXXX.",
    ".............XX.XXXXXX",   # eye
    ".............XXXXXXXXX",
    ".............XXXXXXXXX",
    ".......X.X.X.XXXXX....",   # back spikes
    "......XXXXXXXXXXXXXX..",
    "X....XXXXXXXXXXXXX....",
    "XX..XXXXXXXXXXXXXX.XX.",   # little arm
    ".XXXXXXXXXXXXXXXXX....",
    "..XXXXXXXXXXXXXXX.....",
    "....XXXXXXXXXXXX......",
    ".....XXXX..XXXX.......",
]
_LEGS_A = ["....XX.......XX.......", "....XXX......XXX......"]
_LEGS_B = ["......XX...XX.........", "......XXX..XXX........"]
_BLINK = [r if i != 2 else ".............XXXXXXXXX" for i, r in enumerate(_BODY)]

FRAMES = {k: [row.replace("X", "#") for row in v] for k, v in {
    "a": _BODY + _LEGS_A,
    "b": _BODY + _LEGS_B,
    "stop": _BODY + _LEGS_A,
    "blink": _BLINK + _LEGS_A,
}.items()}

_BITS = {(0, 0): 0x01, (0, 1): 0x02, (0, 2): 0x04, (1, 0): 0x08, (1, 1): 0x10, (1, 2): 0x20, (0, 3): 0x40,
         (1, 3): 0x80}


def to_braille(bitmap: list[str]) -> list[str]:
    """Convert a pixel bitmap to rows of braille chars (each char = 2x4 pixels)."""
    h = len(bitmap)
    w = max(len(r) for r in bitmap)
    rows = []
    for cy in range(0, h, 4):
        line = []
        for cx in range(0, w, 2):
            bits = 0
            for (dx, dy), b in _BITS.items():
                y, x = cy + dy, cx + dx
                if y < h and x < len(bitmap[y]) and bitmap[y][x] == "#":
                    bits |= b
            line.append(chr(0x2800 + bits))
        rows.append("".join(line))
    return rows


SPRITES = {k: to_braille(v) for k, v in FRAMES.items()}
SPRITE_W = len(SPRITES["a"][0])
SPRITE_H = len(SPRITES["a"])

GROUND = "⣀⣀⠤⣀⣀⣀·⣀⣀⠤⠤⣀⣀⣀⣀·⣀⠤⣀⣀"
WALL_SOLID = "⣿"
WALL_DASH = "⡇"
WALL_GHOST = "┆"
SPEED = "⠒"
