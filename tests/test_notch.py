"""Floating notch: placement, expand timing, saved position, autostart, and every state on a real Tk canvas."""
import json
import sys

import pytest

from dinoguard.runner_ui import notch_model as m
from dinoguard.runner_ui import notch_os
from dinoguard.runner_ui.state import STATES

AREA = (0, 0, 1920, 1032)       # 1080p with a 48 px taskbar at the bottom


def test_corners_sit_inside_work_area():
    w, h = m.COLLAPSED
    bl = m.corner_xy("bottom-left", AREA)
    br = m.corner_xy("bottom-right", AREA)
    tc = m.corner_xy("top-center", AREA)
    assert bl == (m.MARGIN, 1032 - h - m.MARGIN)          # above the taskbar, never over it
    assert br == (1920 - w - m.MARGIN, 1032 - h - m.MARGIN)
    assert tc[1] == m.MARGIN and abs((tc[0] + w / 2) - 960) <= 1
    for x, y in (bl, br, tc):
        assert 0 <= x and x + w <= 1920 and 0 <= y and y + h <= 1032


def test_grow_away_from_edges():
    small, big = m.COLLAPSED, m.EXPANDED
    x, y = m.corner_xy("bottom-left", AREA)
    gx, gy = m.grow(x, y, small, big, AREA)
    assert gx == x and gy + big[1] == y + small[1]          # bottom-left: keeps left + bottom edges
    x, y = m.corner_xy("bottom-right", AREA)
    gx, gy = m.grow(x, y, small, big, AREA)
    assert gx + big[0] == x + small[0] and gy + big[1] == y + small[1]
    x, y = m.corner_xy("top-center", AREA)
    gx, gy = m.grow(x, y, small, big, AREA)
    assert gy == y and abs((gx + big[0] / 2) - (x + small[0] / 2)) <= 1
    gx, gy = m.grow(1900, 1020, small, big, AREA)          # off-screen drag still lands inside
    assert gx + big[0] <= 1920 and gy + big[1] <= 1032


def test_position_saved_and_restored(dg_home):
    assert m.load_pos() == {"corner": m.DEFAULT_CORNER}
    m.save_pos({"corner": "bottom-right", "x": 500, "y": 400})
    pos = m.load_pos()
    assert json.loads((dg_home / "notch.json").read_text())["x"] == 500
    assert m.start_xy(pos, AREA) == (500, 400)
    assert m.start_xy({"corner": "top-center", "x": 9999, "y": -50}, AREA) == (1920 - m.COLLAPSED[0], 0)   # clamped
    (dg_home / "notch.json").write_text("{not json")
    assert m.load_pos()["corner"] == m.DEFAULT_CORNER
    m.save_pos({"corner": "sideways"})
    assert m.load_pos()["corner"] == m.DEFAULT_CORNER


def test_expands_for_deny_tip_approval_then_shrinks():
    nm = m.NotchModel()
    nm.feed({"tool": "Bash", "summary": "ls", "decision": "allow"}, now=0)
    assert not nm.expanded(0) and nm.status(0) == "allow · Bash ls"
    nm.feed({"tool": "Bash", "summary": "rm -rf src", "decision": "deny", "policy": "no-rm-rf",
             "reason": "Recursive force delete."}, now=10)
    assert nm.expanded(10) and nm.expanded(13.9) and not nm.expanded(14.1)
    assert "Recursive force delete" in nm.detail() and nm.status(10).startswith("deny")
    nm.feed({"tool": "Read", "summary": "big", "decision": "instruct", "policy": "token-big-output",
             "reason": "grep instead"}, now=20)
    assert nm.expanded(20) and nm.detail() == "grep instead"
    nm.feed({"kind": "approval_wait", "code": "42", "summary": "push"}, now=30)
    assert nm.expanded(30) and nm.status(30).startswith("approve?")
    quiet = m.NotchModel()
    quiet.feed({"decision": "deny", "policy": "x"}, now=0, quiet=True)   # catch-up on start never pops open
    assert not quiet.expanded(0)


def test_status_and_fuel_text():
    nm = m.NotchModel()
    nm.feed({"kind": "guard_down", "reason": "daemon unreachable"}, now=0)
    assert "GUARD DOWN" in nm.status(0)
    frac, cap = m.fuel({"context_tokens": 42_000, "context_window": 200_000, "cost_usd": 0.314})
    assert abs(frac - 0.21) < 1e-9 and cap == "42k/200k · $0.31"
    assert m.fuel({})[0] == 0.0
    assert m.fuel_color(0.9) == m.COLOR["deny"] and m.fuel_color(0.1) == m.COLOR["walk"]
    assert m.ellipsize("x" * 50, 10) == "x" * 9 + "…"
    assert set(m.COLOR) == set(STATES) == set(m.LABEL)


def test_single_instance_lock(dg_home):
    a = notch_os.single_instance()
    assert a is not None
    try:
        assert notch_os.single_instance() is None
    finally:
        a.close()
    b = notch_os.single_instance()
    assert b is not None
    b.close()


def test_autostart_on_and_off(tmp_path, monkeypatch):
    monkeypatch.setenv("DG_STARTUP_DIR", str(tmp_path / "Startup"))
    if sys.platform == "win32":
        # exercise the no-COM fallback (the shortcut code moved to dinoguard/autostart.py, shared with the daemon)
        monkeypatch.setattr("dinoguard.autostart._make_lnk", lambda target, args, description: False)
    p = notch_os.enable_autostart()
    assert p.exists() and p.parent == tmp_path / "Startup"
    text = p.read_text(encoding="utf-8")
    assert "dinoguard" in text and "notch" in text and "--foreground" in text
    assert notch_os.disable_autostart() and not p.exists()
    assert notch_os.disable_autostart() == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcut")
def test_autostart_real_shortcut(tmp_path, monkeypatch):
    monkeypatch.setenv("DG_STARTUP_DIR", str(tmp_path / "Startup"))
    p = notch_os.enable_autostart()
    assert p.suffix in (".lnk", ".vbs") and p.exists()
    notch_os.disable_autostart()
    assert not p.exists()


@pytest.fixture(scope="module")
def _tk_app():
    tk = pytest.importorskip("tkinter")
    try:
        app = tk.Tk()             # one Tk per process, like the real notch
    except tk.TclError as exc:
        pytest.skip(f"no display for tkinter: {exc}")
    app.withdraw()
    yield app
    app.destroy()


@pytest.fixture
def tk_root(_tk_app):
    import tkinter as tk
    win = tk.Toplevel(_tk_app)
    yield win
    try:
        win.destroy()
    except tk.TclError:
        pass


class _NoTail:
    def __init__(self, recs=None):
        self.recs = list(recs or [])

    def read(self):
        out, self.recs = self.recs, []
        return out


_RECS = {
    "walk": {"tool": "Bash", "summary": "ls", "decision": "allow"},
    "sprint": {"tool": "Read", "summary": "x", "decision": "instruct", "policy": "token-big-output", "reason": "tip"},
    "instruct": {"tool": "Bash", "summary": "DROP", "decision": "instruct", "policy": "warn-destructive-sql",
                 "reason": "backup first " * 30},
    "deny": {"tool": "Bash", "summary": "rm -rf src", "decision": "deny", "policy": "no-rm-rf", "reason": "no"},
    "shield": {"tool": "Bash", "summary": "x", "decision": "deny", "policy": "guard-self-protect", "reason": "no"},
    "review": {"kind": "review", "summary": "kubectl apply"},
    "wait": {"kind": "approval_wait", "code": "7", "summary": "push"},
    "ghost": {"tool": "Bash", "summary": "x", "decision": "allow",
              "observed": [{"policy": "p", "decision": "deny"}]},
    "guard_down": {"kind": "guard_down", "reason": "daemon unreachable"},
}


@pytest.mark.parametrize("state", STATES)
def test_every_state_renders_on_canvas(tk_root, state):
    from dinoguard.runner_ui.notch import Notch
    rec = dict(_RECS[state], tokens={"context_tokens": 170_000, "context_window": 200_000, "cost_usd": 1.5})
    tail = _NoTail()
    n = Notch(tk_root, tail=tail)          # start-up catch-up is quiet, so feed the record live
    tail.recs.append(rec)
    for _ in range(30):
        n.frame()
        tk_root.update_idletasks()
    assert n.model.state() == state
    if state in m.EXPAND_ON:
        assert n.size == m.EXPANDED
    n.model.expand_until = 0
    n.frame()
    assert n.size == m.COLLAPSED


def test_drag_saves_position_and_corner_menu(tk_root, dg_home):
    from dinoguard.runner_ui.notch import Notch

    class E:
        def __init__(self, x, y):
            self.x_root, self.y_root = x, y
    n = Notch(tk_root, tail=_NoTail())
    x0, y0 = n.anchor
    n._press(E(x0 + 5, y0 + 5))
    n._motion(E(x0 + 105, y0 - 95))
    n._release(E(x0 + 105, y0 - 95))
    saved = json.loads((dg_home / "notch.json").read_text())
    assert (saved["x"], saved["y"]) == n.anchor == m.clamp(x0 + 100, y0 - 100, m.COLLAPSED, n.area)
    n.set_corner("bottom-right")
    assert m.load_pos() == {"corner": "bottom-right"}
    assert n.anchor == m.corner_xy("bottom-right", n.area)


def test_new_sprite_shape_and_gait():
    from dinoguard.runner_ui.art import FRAMES
    from dinoguard.runner_ui.notch import PIXELS, frame_for
    for name, rows in FRAMES.items():
        assert len(rows) == 15 and all(len(r) == 22 for r in rows), name
    assert FRAMES["a"][:13] == FRAMES["b"][:13] and FRAMES["a"][13:] != FRAMES["b"][13:]   # only legs move
    assert {frame_for("walk", t) for t in range(12)} == {"a", "b"}
    assert frame_for("deny", 0) == "stop" and "blink" in {frame_for("deny", t) for t in range(16)}
    assert PIXELS["blink"] - PIXELS["stop"] == {(15, 2)}                                    # the eye closes


def test_palette_and_grey_blink():
    from dinoguard.runner_ui.notch import dino_color
    assert m.BG == "#1F1F1E" and m.COLLAPSED == (230, 46) and m.EXPANDED == (330, 104)
    assert dino_color("walk", 0) == "#97C459" and dino_color("sprint", 0) == "#EF9F27"
    assert dino_color("wait", 0) == "#85B7EB"
    for st in ("deny", "shield"):
        assert {dino_color(st, t) for t in range(20)} == {m.DINO_GREY, m.DINO_DIM}
    assert m.COLOR["deny"] == "#E24B4A" and m.COLOR["shield"] == "#7F77DD"


def test_shape_has_square_top_and_round_bottom():
    from dinoguard.runner_ui.notch import shape_points
    pts = shape_points(230, 46)
    xy = list(zip(pts[::2], pts[1::2]))
    assert (0, 0) in xy and (230, 0) in xy                 # square top corners
    assert (230, 46) not in xy and (0, 46) not in xy       # rounded bottom corners
    assert all(0 <= x <= 230 and 0 <= y <= 46 for x, y in xy)


def test_ease_transition_takes_035s(tk_root):
    from dinoguard.runner_ui.notch import Notch
    assert m.ease(0) == 0 and m.ease(1) == 1 and 0.4 < m.ease(0.5) < 0.6
    n = Notch(tk_root, tail=_NoTail())
    n.resize(m.EXPANDED, now=100.0)
    target = n.anim[1]
    n._step_anim(100.0 + 0.17)
    mid = n.rect
    assert m.COLLAPSED[0] < mid[2] < m.EXPANDED[0] and n.anim is not None
    n._step_anim(100.0 + m.TRANSITION_S + 0.01)
    assert n.rect == target and n.anim is None
