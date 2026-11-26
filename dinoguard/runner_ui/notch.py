"""`dinoguard notch`: a small always-on-top pill with the dotted dino, beside your other apps.

tkinter only (standard library). Reads the same stream.jsonl and uses the same states as the
terminal runner. The dino is drawn as round dots on a Canvas; each frame only touches the items
that changed, so 20 fps stays cheap. Size changes ease over 0.35 s.
"""
from __future__ import annotations

import math
import sys
import time
import webbrowser

from .. import config, paths
from . import notch_model as m
from .app import StreamTail
from .art import FRAMES
from .state import RunnerState

PITCH, DOT = 2.5, 2.2           # px between dino dots, dot diameter
DINO_X, DINO_Y = 12, 4
DINO_W = 22 * PITCH
WALL_X = DINO_X + DINO_W + 4
TEXT_X = WALL_X + 10
GROUND_Y = DINO_Y + 15 * PITCH + 1.5
RADIUS = 20                     # bottom corners only
GUARD_BG = "#A32D2D"
_GAIT = {"walk": (3, 1.0), "ghost": (3, 1.0), "sprint": (1, 3.0), "instruct": (6, 0.4), "review": (5, 0.5)}
_DINO_COLOR = {"walk": "walk", "ghost": "walk", "sprint": "sprint", "wait": "wait", "instruct": "instruct",
               "review": "review"}


def _pixels(bitmap: list[str]) -> frozenset[tuple[int, int]]:
    return frozenset((x, y) for y, row in enumerate(bitmap) for x, ch in enumerate(row) if ch == "#")


PIXELS = {k: _pixels(v) for k, v in FRAMES.items()}
ALL_PIXELS = sorted(frozenset().union(*PIXELS.values()))


def frame_for(state: str, tick: int) -> str:
    if state in _GAIT:
        every, _ = _GAIT[state]
        return "a" if (tick // every) % 2 == 0 else "b"
    if state in m.BLINK_GREY and tick % 16 >= 13:
        return "blink"
    return "stop"


def dino_color(state: str, tick: int) -> str:
    if state in m.BLINK_GREY:                       # grey, blinking behind the wall
        return m.DINO_GREY if (tick // 8) % 2 == 0 else m.DINO_DIM
    if state == "guard_down":
        return "#ffffff"
    return m.COLOR[_DINO_COLOR.get(state, "walk")]


def shape_points(w: int, h: int, r: int = RADIUS) -> list[float]:
    """Square top corners, rounded bottom corners (radius r)."""
    r = max(0, min(r, h // 2, w // 2))
    pts = [0.0, 0.0, float(w), 0.0, float(w), float(h - r)]
    for i in range(1, 9):                           # bottom-right arc
        a = math.pi / 2 * i / 8
        pts += [w - r + r * math.cos(a), h - r + r * math.sin(a)]
    for i in range(0, 9):                           # bottom-left arc
        a = math.pi / 2 + math.pi / 2 * i / 8
        pts += [r + r * math.cos(a), h - r + r * math.sin(a)]
    return pts


class Notch:
    def __init__(self, root, tail: StreamTail | None = None, model: m.NotchModel | None = None, fps: int = 20):
        import tkinter as tk
        self.tk, self.root = tk, root
        cfg = config.load()
        burn = float((cfg.get("runner") or {}).get("burn_high_per_min", 20000))
        self.model = model or m.NotchModel(RunnerState(burn_high_per_min=burn))
        self.tail = tail if tail is not None else StreamTail(paths.stream_path())
        self.frame_ms = max(10, int(1000 / max(1, fps)))
        self.tick, self._job, self._hidden_job = 0, None, None
        self.pos = m.load_pos()
        self.area = m.work_area(root.winfo_screenwidth(), root.winfo_screenheight())
        self.anchor = m.start_xy(self.pos, self.area)
        self.size = m.COLLAPSED
        self.rect = (*self.anchor, *m.COLLAPSED)
        self.anim = None                                # (from_rect, to_rect, t0)
        self._drag = None
        self._cache: dict = {}
        self._shown: frozenset = frozenset()
        self._painted = None
        self.transparent = sys.platform == "win32"

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        if self.transparent:
            root.configure(bg=m.KEY)
            root.attributes("-transparentcolor", m.KEY)
        self.canvas = tk.Canvas(root, highlightthickness=0, bd=0,
                                bg=m.KEY if self.transparent else m.BG, cursor="fleur")
        self.canvas.pack(fill="both", expand=True)
        self._build()
        self._menu()
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._popup)
        self.canvas.bind("<Double-Button-1>", lambda e: self.open_dashboard())
        for rec in self.tail.read():                      # catch up quietly (tokens, last action)
            self.model.feed(rec, time.monotonic() - 100, quiet=True)
        self._apply(self.rect)

    # ---- building (once) -------------------------------------------------------------------------

    def _build(self):
        c = self.canvas
        self.bg = c.create_polygon(shape_points(*m.COLLAPSED), fill=m.BG, outline="")
        self.dots = {}
        for (px, py) in ALL_PIXELS:
            x0, y0 = DINO_X + px * PITCH, DINO_Y + py * PITCH
            self.dots[(px, py)] = c.create_oval(x0, y0, x0 + DOT, y0 + DOT, width=0, state="hidden",
                                                tags=("dino",))
        self.ground = [c.create_oval(0, 0, 0, 0, width=0, fill=m.TRACK) for _ in range(12)]
        self.wall = [c.create_oval(WALL_X, DINO_Y + i * 4.2, WALL_X + 3, DINO_Y + i * 4.2 + 3, width=0,
                                   state="hidden") for i in range(9)]
        self.speed = [c.create_line(DINO_X - 8 + i * 2, DINO_Y + 16 + i * 6, DINO_X - 1 + i * 2,
                                    DINO_Y + 16 + i * 6, fill=m.COLOR["sprint"], state="hidden") for i in range(3)]
        head_x = DINO_X + 18 * PITCH
        self.qmark = c.create_text(head_x, DINO_Y - 1, text="?", fill=m.COLOR["wait"], anchor="s",
                                   font=("Segoe UI", 10, "bold"), state="hidden")
        self.bar_w = m.COLLAPSED[0] - TEXT_X - 14
        self.status = c.create_text(TEXT_X, 14, anchor="w", fill=m.FG, font=("Segoe UI", 9, "bold"))
        self.caption = c.create_text(TEXT_X, 27, anchor="w", fill=m.MUTED, font=("Segoe UI", 8))
        c.create_rectangle(TEXT_X, 35, TEXT_X + self.bar_w, 38, width=0, fill=m.TRACK)
        self.bar = c.create_rectangle(TEXT_X, 35, TEXT_X, 38, width=0, fill=m.COLOR["walk"])
        self.detail = c.create_text(14, 49, anchor="nw", width=m.EXPANDED[0] - 28, fill=m.FG,
                                    font=("Segoe UI", 8), state="hidden")

    def _cfg(self, item, **kw):
        """itemconfigure only when something changed (every redraw costs CPU)."""
        key = tuple(sorted(kw.items()))
        if self._cache.get(item) != key:
            self._cache[item] = key
            self.canvas.itemconfigure(item, **kw)

    def _xy(self, item, *pts):
        if self._cache.get(("c", item)) != pts:
            self._cache[("c", item)] = pts
            self.canvas.coords(item, *pts)

    # ---- geometry + 0.35 s ease ------------------------------------------------------------------

    def _target(self, size) -> tuple[int, int, int, int]:
        if size == m.COLLAPSED:
            return (*self.anchor, *size)
        return (*m.grow(*self.anchor, m.COLLAPSED, size, self.area), *size)

    def _apply(self, rect):
        x, y, w, h = rect
        self.rect = rect
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self._xy(self.bg, *shape_points(w, h))
        open_enough = h >= m.COLLAPSED[1] + 40 and self.size != m.COLLAPSED
        self._cfg(self.detail, state="normal" if open_enough else "hidden")

    def resize(self, size, now: float | None = None, animate: bool = True):
        now = time.monotonic() if now is None else now
        self.size = size
        target = self._target(size)
        if animate:
            self.anim = (self.rect, target, now)
        else:
            self.anim = None
            self._apply(target)

    def _step_anim(self, now: float):
        if not self.anim:
            return
        a, b, t0 = self.anim
        k = (now - t0) / m.TRANSITION_S
        self._apply(m.lerp_rect(a, b, m.ease(k)))
        if k >= 1:
            self.anim = None

    # ---- per frame -----------------------------------------------------------------------------

    def frame(self, now: float | None = None):
        now = time.monotonic() if now is None else now
        for rec in self.tail.read():
            self.model.feed(rec, now)
        st = self.model.state(now)
        want = m.EXPANDED if self.model.expanded(now) else m.COLLAPSED
        if want != self.size:
            self.resize(want, now)
        self._step_anim(now)
        self._paint(st)
        self._animate(st)
        self.tick += 1
        if self.tick % 100 == 0:                        # stay above apps that grab the top spot
            self.root.attributes("-topmost", True)

    def _paint(self, st: str):
        if self._painted != st:
            down = st == "guard_down"
            self.canvas.itemconfigure(self.bg, fill=GUARD_BG if down else m.BG)
            for wpart in self.wall:
                self.canvas.itemconfigure(wpart, fill=m.COLOR[st])
            ground = "#ffffff" if down else m.COLOR["review"] if st == "review" else m.TRACK
            for g in self.ground:
                self.canvas.itemconfigure(g, fill=ground)
            self._cfg(self.status, fill="#ffffff" if down else m.COLOR[st])
            self._painted = st
        frac, cap = m.fuel(self.model.runner.tokens)
        chars = max(10, int((self.size[0] - TEXT_X - 14) / 6.3))
        self._cfg(self.status, text=m.ellipsize(self.model.status(), chars),
                  fill="#ffffff" if st == "guard_down" else m.COLOR[st])
        self._cfg(self.caption, text=cap)
        self._xy(self.bar, TEXT_X, 35, TEXT_X + max(1, int(self.bar_w * frac)), 38)
        self._cfg(self.bar, fill=m.fuel_color(frac))
        if self.size != m.COLLAPSED:
            self._cfg(self.detail, text=m.ellipsize(self.model.detail(), 150),
                      state="normal" if self.rect[3] >= m.COLLAPSED[1] + 40 else "hidden")

    def _animate(self, st: str):
        c, t = self.canvas, self.tick
        show = PIXELS[frame_for(st, t)]
        for p in show ^ self._shown:                     # toggle only the dots that changed
            c.itemconfigure(self.dots[p], state="normal" if p in show else "hidden")
        self._shown = show
        col = dino_color(st, t)
        if self._cache.get("dino") != col:
            self._cache["dino"] = col
            c.itemconfigure("dino", fill=col)
        speed = _GAIT.get(st, (0, 0.0))[1]
        off = (t * speed) % 5
        scan = (t * 2) % 60 if st == "review" else -99
        for i, g in enumerate(self.ground):
            x = DINO_X + 4 + i * 5 - off
            r = 1.6 if abs(x - (DINO_X + 4 + scan)) < 3 else 0.9
            self._xy(g, round(x - r, 1), round(GROUND_Y - r, 1), round(x + r, 1), round(GROUND_Y + r, 1))
        wall = {"deny": "solid", "shield": "solid", "instruct": "dash", "ghost": "dash"}.get(st)
        for i, wpart in enumerate(self.wall):
            on = wall == "solid" or (wall == "dash" and i % 2 == 0)
            self._cfg(wpart, state="normal" if on else "hidden")
        for s in self.speed:
            self._cfg(s, state="normal" if st == "sprint" else "hidden")
        self._cfg(self.qmark, state="normal" if st == "wait" and (t // 8) % 2 == 0 else "hidden")

    # ---- interaction -----------------------------------------------------------------------------

    def _press(self, e):
        self.model.expand_until = 0.0
        if self.size != m.COLLAPSED or self.anim:
            self.resize(m.COLLAPSED, animate=False)
        self._drag = (e.x_root - self.anchor[0], e.y_root - self.anchor[1], e.x_root, e.y_root, False)

    def _motion(self, e):
        if not self._drag:
            return
        dx, dy, sx, sy, moved = self._drag
        moved = moved or abs(e.x_root - sx) + abs(e.y_root - sy) > 3
        self._drag = (dx, dy, sx, sy, moved)
        if moved:
            self.anchor = m.clamp(e.x_root - dx, e.y_root - dy, m.COLLAPSED, self.area)
            self._apply((*self.anchor, *m.COLLAPSED))

    def _release(self, e):
        if self._drag and self._drag[4]:
            self.pos.update(x=self.anchor[0], y=self.anchor[1])
            m.save_pos(self.pos)
        elif self.model.detail():                        # plain click: show the last reason again
            self.model.expand_until = time.monotonic() + m.EXPAND_S
        self._drag = None

    def set_corner(self, corner: str):
        self.pos = {"corner": corner}
        m.save_pos(self.pos)
        self.anchor = m.corner_xy(corner, self.area)
        self.resize(self.size, animate=False)

    def open_dashboard(self):
        webbrowser.open("http://127.0.0.1:8030")

    def hide(self, minutes: int = 10):
        self.root.withdraw()
        self._hidden_job = self.root.after(minutes * 60_000, self._unhide)

    def _unhide(self):
        self.root.deiconify()
        self.root.attributes("-topmost", True)

    def _menu(self):
        tk = self.tk
        self.menu = tk.Menu(self.root, tearoff=0)
        corners = tk.Menu(self.menu, tearoff=0)
        self._corner_var = tk.StringVar(value=self.pos.get("corner", m.DEFAULT_CORNER))
        for name in m.CORNERS:
            corners.add_radiobutton(label=name, variable=self._corner_var, value=name,
                                    command=lambda n=name: self.set_corner(n))
        self.menu.add_cascade(label="Corner", menu=corners)
        self.menu.add_command(label="Open dashboard", command=self.open_dashboard)
        self.menu.add_command(label="Hide for 10 min", command=self.hide)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.root.destroy)

    def _popup(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    # ---- loop ------------------------------------------------------------------------------------

    def _loop(self):
        try:
            self.frame()
        finally:                                        # ~60 fps only while easing, else 20 fps
            self._job = self.root.after(16 if self.anim else self.frame_ms, self._loop)

    def start(self):
        self._loop()


def run(fps: int = 20, max_seconds: float | None = None) -> int:
    import tkinter as tk
    from .notch_os import single_instance
    lock = single_instance()
    if lock is None:
        print("dinoguard notch is already running.")
        return 0
    root = tk.Tk()
    root.title("DinoGuard")
    notch = Notch(root, fps=fps)
    notch.start()
    if max_seconds:
        root.after(int(max_seconds * 1000), root.destroy)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        lock.close()
    return 0
