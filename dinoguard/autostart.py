"""Open-on-login entries for DinoGuard background apps (the notch and the daemon).

Windows: a Startup-folder shortcut (.lnk via PowerShell, .vbs fallback). macOS: a LaunchAgent plist. Linux: an
XDG autostart .desktop file. `DG_STARTUP_DIR` redirects the folder (tests).
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

APPS = {   # key -> (Windows shortcut name, macOS label, Linux file stem, description)
    "notch": ("DinoGuard notch", "dev.dinoguard.notch", "dinoguard-notch", "DinoGuard floating dino"),
    "daemon": ("DinoGuard daemon", "dev.dinoguard.daemon", "dinoguard-daemon", "DinoGuard guard daemon"),
}


def gui_python() -> str:
    """pythonw.exe on Windows (no console window), else the current interpreter."""
    exe = Path(sys.executable)
    if sys.platform == "win32":
        w = exe.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return str(exe)


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


def path(app: str) -> Path:
    win, mac, linux, _ = APPS[app]
    d = startup_dir()
    if sys.platform == "darwin" and not os.environ.get("DG_STARTUP_DIR"):
        return d / f"{mac}.plist"
    if sys.platform == "win32":
        return d / f"{win}.lnk"
    return d / f"{linux}.desktop"


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _make_lnk(target: Path, args: list[str], description: str) -> bool:
    exe, rest = args[0], subprocess.list2cmdline(args[1:])
    script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(target))});"
              f"$s.TargetPath={_ps_quote(exe)};$s.Arguments={_ps_quote(rest)};"
              f"$s.WorkingDirectory={_ps_quote(str(Path.home()))};"
              f"$s.WindowStyle=7;$s.Description={_ps_quote(description)};$s.Save()")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and target.exists()


def enable(app: str, args: list[str]) -> Path:
    win, mac, linux, description = APPS[app]
    target = path(app)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix == ".lnk":
        if _make_lnk(target, args, description):
            return target
        target = target.with_suffix(".vbs")            # fallback: hidden-window script
        cmd = subprocess.list2cmdline(args).replace('"', '""')
        target.write_text(f'CreateObject("WScript.Shell").Run "{cmd}", 0, False\r\n', encoding="utf-8")
    elif target.suffix == ".plist":
        items = "".join(f"<string>{a}</string>" for a in args)
        target.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
            f"<key>Label</key><string>{mac}</string>"
            f"<key>ProgramArguments</key><array>{items}</array>"
            "<key>RunAtLoad</key><true/></dict></plist>\n", encoding="utf-8")
    else:
        target.write_text(f"[Desktop Entry]\nType=Application\nName={win}\n"
                          f"Exec={shlex.join(args)}\nX-GNOME-Autostart-enabled=true\n", encoding="utf-8")
    return target


def disable(app: str) -> list[Path]:
    base = path(app)
    removed = []
    for p in {base, base.with_suffix(".vbs")}:
        if p.exists():
            p.unlink()
            removed.append(p)
    return removed


def is_enabled(app: str) -> bool:
    base = path(app)
    return base.exists() or base.with_suffix(".vbs").exists()
