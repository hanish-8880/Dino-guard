"""Original dotted dino ("Dot", a little plated guard-dino) drawn on a pixel grid, rendered as braille."""
from __future__ import annotations

# 16 x 12 pixel frames, '#' = dot on. Head on the right, back plates on top, tail on the left.
_BODY = [
    "...........######...",
    "..........########..",
    "..........##.#####..",   # eye
    "...#..#..#########..",   # back plates
    "..###.###.######....",
    "#############.......",
    ".############.......",
    "..##########........",
]
_LEGS_A = ["..###....###........", "..##.....##.........", ".###....###.........", "...................."]
_LEGS_B = ["...###..###.........", "....##..##..........", "....###.###.........", "...................."]
_LEGS_STOP = ["..###.....###.......", "..##......##........", ".###.....###........", "...................."]
_BLINK = [r if i != 2 else "..........########.." for i, r in enumerate(_BODY)]

FRAMES = {
    "a": _BODY + _LEGS_A,
    "b": _BODY + _LEGS_B,
    "stop": _BODY + _LEGS_STOP,
    "blink": _BLINK + _LEGS_STOP,
}

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
