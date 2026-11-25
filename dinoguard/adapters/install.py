"""Write / remove DinoGuard hooks in harness settings. Merge, never clobber; back up first;
uninstall restores the original bytes exactly when nothing else changed since install."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from .. import paths
from ..util import now_iso

MARKER = "dinoguard"
CLAUDE_EVENTS = ["PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest", "UserPromptSubmit",
                 "Stop", "SubagentStop", "SubagentStart", "SessionStart", "SessionEnd", "PreCompact", "Notification"]
MATCHER_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"}


def hook_command(harness: str = "claude") -> str:
    exe = shutil.which("dinoguard-hook")
    if exe:
        exe = exe.replace("\\", "/")
        return f'"{exe}" {harness}' if " " in exe else f"{exe} {harness}"
    py = sys.executable.replace("\\", "/")
    py = f'"{py}"' if " " in py else py
    return f"{py} -m dinoguard.hook {harness}"


def _state_path() -> Path:
    return paths.home() / "install_state.json"


def _load_state() -> dict:
    p = _state_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def _save_state(st: dict) -> None:
    _state_path().write_text(json.dumps(st, indent=2), encoding="utf-8")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def is_ours(handler: dict) -> bool:
    return MARKER in str(handler.get("command", "")).lower()


def strip_ours(settings: dict) -> dict:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    for ev in list(hooks):
        groups = hooks.get(ev)
        if not isinstance(groups, list):
            continue
        new_groups = []
        for g in groups:
            if not isinstance(g, dict):
                new_groups.append(g)
                continue
            hs = [h for h in g.get("hooks", []) if not (isinstance(h, dict) and is_ours(h))]
            if hs:
                new_groups.append({**g, "hooks": hs})
            elif not g.get("hooks"):
                new_groups.append(g)
        if new_groups:
            hooks[ev] = new_groups
        else:
            del hooks[ev]
    if not hooks:
        del settings["hooks"]
    return settings


def merged_settings(original: bytes | None, harness: str = "claude", events=CLAUDE_EVENTS) -> dict:
    settings = json.loads(original.decode("utf-8")) if original and original.strip() else {}
    if not isinstance(settings, dict):
        raise ValueError("settings file is not a JSON object")
    settings = strip_ours(settings)
    hooks = settings.setdefault("hooks", {})
    cmd = hook_command(harness)
    for ev in events:
        group: dict = {"hooks": [{"type": "command", "command": cmd}]}
        if ev in MATCHER_EVENTS:
            group = {"matcher": "*", **group}
        hooks.setdefault(ev, []).append(group)
    return settings


def install_claude(scope: str = "user", cwd: str | None = None) -> dict:
    target = paths.claude_settings_path(scope, cwd)
    existed = target.exists()
    original = target.read_bytes() if existed else None
    state = _load_state()
    key = str(target)
    prev = state.get(key)
    if original is not None:
        bk = paths.backups_dir() / f"claude-{scope}-settings-{now_iso().replace(':', '-')}.json"
        bk.write_bytes(original)
    settings = merged_settings(original)
    out = (json.dumps(settings, indent=2) + "\n").encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".dg-tmp")
    tmp.write_bytes(out)
    os.replace(tmp, target)
    # keep the *first* original across repeated installs
    if prev and prev.get("written_sha") == (_sha(original) if original is not None else None):
        orig_b64 = prev.get("original")
    else:
        orig_b64 = base64.b64encode(original).decode() if original is not None else None
    state[key] = {"harness": "claude", "scope": scope, "original": orig_b64, "written_sha": _sha(out),
                  "installed_at": now_iso()}
    _save_state(state)
    return {"settings": str(target), "command": hook_command("claude"), "events": CLAUDE_EVENTS}


def uninstall_claude(scope: str = "user", cwd: str | None = None) -> dict:
    target = paths.claude_settings_path(scope, cwd)
    state = _load_state()
    key = str(target)
    entry = state.get(key)
    if not target.exists():
        state.pop(key, None)
        _save_state(state)
        return {"settings": key, "action": "nothing to remove"}
    current = target.read_bytes()
    if entry and entry.get("written_sha") == _sha(current):
        if entry.get("original") is None:
            target.unlink()
            action = "removed settings file created by install"
        else:
            target.write_bytes(base64.b64decode(entry["original"]))
            action = "restored original settings byte-for-byte"
    else:
        settings = json.loads(current.decode("utf-8") or "{}")
        settings = strip_ours(settings)
        target.write_bytes((json.dumps(settings, indent=2) + "\n").encode("utf-8"))
        action = "settings changed since install; removed only DinoGuard hooks"
    state.pop(key, None)
    _save_state(state)
    return {"settings": key, "action": action}


def wiring(scope: str = "user", cwd: str | None = None) -> dict:
    """Which events have a DinoGuard hook in the given settings file."""
    target = paths.claude_settings_path(scope, cwd)
    if not target.exists():
        return {"settings": str(target), "present": False, "events": {}}
    try:
        settings = json.loads(target.read_text(encoding="utf-8") or "{}")
    except ValueError as e:
        return {"settings": str(target), "present": True, "error": f"invalid JSON: {e}", "events": {}}
    found = {}
    for ev in CLAUDE_EVENTS:
        cmds = [h.get("command") for g in (settings.get("hooks") or {}).get(ev, []) if isinstance(g, dict)
                for h in g.get("hooks", []) if isinstance(h, dict) and is_ours(h)]
        found[ev] = cmds
    return {"settings": str(target), "present": True, "events": found,
            "disable_all": bool(settings.get("disableAllHooks"))}
