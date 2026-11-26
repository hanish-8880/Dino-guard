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
    exe = Path(sys.executable)
    if sys.platform == "win32":
        w = exe.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return str(exe)


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


# ---- open on login ---------------------------------------------------------------------------

def startup_dir() -> Path:
    override = os.environ.get("DG_STARTUP_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / \
            "Microsoft/Windows/Start Menu/Programs/Startup"
    if sys.platform == "darwin":
        return Path.home() / "Library/LaunchAgents"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"


def autostart_path() -> Path:
    d = startup_dir()
    if sys.platform == "darwin" and not os.environ.get("DG_STARTUP_DIR"):
        return d / "dev.dinoguard.notch.plist"
    if sys.platform == "win32":
        return d / f"{APP_NAME}.lnk"
    return d / "dinoguard-notch.desktop"


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _make_lnk(target: Path, args: list[str]) -> bool:
    exe, rest = args[0], subprocess.list2cmdline(args[1:])
    script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(target))});"
              f"$s.TargetPath={_ps_quote(exe)};$s.Arguments={_ps_quote(rest)};"
              f"$s.WorkingDirectory={_ps_quote(str(Path.home()))};"
              f"$s.Description={_ps_quote('DinoGuard floating dino')};$s.Save()")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and target.exists()


def enable_autostart() -> Path:
    target = autostart_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    args = launch_args()
    if target.suffix == ".lnk":
        if _make_lnk(target, args):
            return target
        target = target.with_suffix(".vbs")            # fallback: hidden-window script
        cmd = subprocess.list2cmdline(args).replace('"', '""')
        target.write_text(f'CreateObject("WScript.Shell").Run "{cmd}", 0, False\r\n', encoding="utf-8")
    elif target.suffix == ".plist":
        items = "".join(f"<string>{a}</string>" for a in args)
        target.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
            "<key>Label</key><string>dev.dinoguard.notch</string>"
            f"<key>ProgramArguments</key><array>{items}</array>"
            "<key>RunAtLoad</key><true/></dict></plist>\n", encoding="utf-8")
    else:
        import shlex
        target.write_text("[Desktop Entry]\nType=Application\nName=DinoGuard notch\n"
                          f"Exec={shlex.join(args)}\nX-GNOME-Autostart-enabled=true\n", encoding="utf-8")
    return target


def disable_autostart() -> list[Path]:
    base = autostart_path()
    removed = []
    for p in {base, base.with_suffix(".vbs")}:
        if p.exists():
            p.unlink()
            removed.append(p)
    return removed
