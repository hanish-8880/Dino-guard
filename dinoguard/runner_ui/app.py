"""`dinoguard runner`: tail stream.jsonl and animate the dino with rich.live."""
from __future__ import annotations

import json
import os
import time

from .. import config, paths
from .scene import render
from .state import RunnerState


class StreamTail:
    """Follow a JSONL file; survives rotation/truncation and the file not existing yet."""

    def __init__(self, path, backlog: int = 50):
        self.path = str(path)
        self.pos = 0
        self.ident = None
        self.backlog = backlog
        self._buf = b""

    def _ident(self):
        try:
            st = os.stat(self.path)
            return (st.st_ino, st.st_dev) if st.st_ino else None, st.st_size
        except OSError:
            return None, -1

    def read(self) -> list[dict]:
        ident, size = self._ident()
        if size < 0:
            self.pos, self.ident = 0, None
            return []
        if self.ident is None:
            self.ident = ident
            self.pos = max(0, size - 64 * 1024) if self.backlog else size
        elif ident != self.ident or size < self.pos:
            self.ident, self.pos, self._buf = ident, 0, b""
        if size == self.pos:
            return []
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            data = f.read()
        self.pos += len(data)
        data = self._buf + data
        lines = data.split(b"\n")
        self._buf = lines.pop()
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        if self.backlog:
            out = out[-self.backlog:]
            self.backlog = 0
        return out


def run(fps: int = 20, max_seconds: float | None = None) -> int:
    from rich.console import Console
    from rich.live import Live
    cfg = config.load()
    burn_high = float((cfg.get("runner") or {}).get("burn_high_per_min", 20000))
    st = RunnerState(burn_high_per_min=burn_high)
    tail = StreamTail(paths.stream_path())
    for rec in tail.read():          # catch up on tokens / last state quietly
        st.feed(rec, time.monotonic() - 100)
    console = Console()
    tick, frame_s = 0, 1.0 / max(1, fps)
    start = time.monotonic()
    with Live(console=console, refresh_per_second=fps, transient=False, auto_refresh=False) as live:
        try:
            while max_seconds is None or time.monotonic() - start < max_seconds:
                t0 = time.monotonic()
                for rec in tail.read():
                    st.feed(rec)
                state = st.current()
                canvas = render(state, console.width, tick, st.message, st.tip, st.tokens, st.last, burn_high)
                live.update(canvas.to_text(), refresh=True)
                tick += 1
                time.sleep(max(0.0, frame_s - (time.monotonic() - t0)))
        except KeyboardInterrupt:
            pass
    return 0
