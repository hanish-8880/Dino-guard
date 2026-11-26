"""OS glue for the notch: one instance at a time, background launch, open-on-login."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .. import paths

APP_NAME = "DinoGuard notch"


def gui_python() -> str:
    """pythonw.exe on Windows (no console window), else the current interpreter."""
    from ..autostart import gui_python as _g
    return _g()


def launch_args() -> list[str]:
    return [gui_python(), "-m", "dinoguard", "notch", "--foreground"]


def single_instance():
    """Hold an OS file lock while the notch runs. Returns the open lock file, or None if taken."""
    p = paths.home() / "notch.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    f = open(p, "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def spawn_detached() -> int:
    """Start the notch in the background so the terminal is free. Returns the child pid."""
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "close_fds": True}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(launch_args(), **kw).pid


# ---- open on login (shared with the daemon: dinoguard/autostart.py) ---------------------------

def startup_dir() -> Path:
    from .. import autostart
    return autostart.startup_dir()


def autostart_path() -> Path:
    from .. import autostart
    return autostart.path("notch")


def enable_autostart() -> Path:
    from .. import autostart
    return autostart.enable("notch", launch_args())


def disable_autostart() -> list[Path]:
    from .. import autostart
    return autostart.disable("notch")
