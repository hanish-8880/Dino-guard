"""Installers for the Phase 13 harnesses (CLAUDE.md §17.10): Devin, Factory Droid, Deep Agents Code, Antigravity,
Pi and OpenClaw. Hermes is printed as a snippet (its config.yaml is the user's own file with comments, so we don't
rewrite it). Same guarantees as the other installers: back up, merge, restore the exact bytes on uninstall."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .. import paths
from .install import hook_command, is_ours

MORE = ("devin", "droid", "deepagents", "antigravity", "pi", "openclaw", "goose")
FILE_ONLY = ("pi", "openclaw", "goose")          # files DinoGuard owns outright: uninstall deletes them

DEVIN_EVENTS = ["PreToolUse", "PostToolUse", "PermissionRequest", "UserPromptSubmit", "Stop", "SessionStart",
                "SessionEnd"]
DROID_EVENTS = ["PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop", "PreCompact",
                "SessionStart", "SessionEnd", "Notification"]
DEEPAGENTS_EVENTS = ["PreToolUse", "PostToolUse", "PermissionRequest", "UserPromptSubmit", "Stop", "SubagentStop",
                     "PreCompact", "SessionStart", "SessionEnd"]
TOOL_EVENTS = {"PreToolUse", "PostToolUse", "PermissionRequest"}


def _home(env: str, default: str) -> Path:
    return Path(os.environ[env]) if os.environ.get(env) else Path.home() / default


def _devin_user() -> Path:
    if os.environ.get("DG_DEVIN_HOME"):
        return Path(os.environ["DG_DEVIN_HOME"]) / "config.json"
    if sys.platform.startswith("win") and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "devin" / "config.json"
    return Path.home() / ".config" / "devin" / "config.json"


def target(harness: str, scope: str, cwd: str) -> Path:
    root, user = Path(cwd), scope == "user"
    if harness == "devin":
        return _devin_user() if user else root / ".devin" / "hooks.v1.json"
    if harness == "droid":
        return _home("DG_DROID_HOME", ".factory") / "hooks.json" if user else root / ".factory" / "hooks.json"
    if harness == "deepagents":
        return _home("DG_DEEPAGENTS_HOME", ".deepagents") / "hooks.json" if user else root / ".deepagents" / "hooks.json"
    if harness == "antigravity":
        return _home("DG_ANTIGRAVITY_HOME", ".gemini/config") / "hooks.json" if user else root / ".agents" / "hooks.json"
    if harness == "pi":
        return (_home("DG_PI_HOME", ".pi/agent") / "extensions" / "dinoguard.js" if user
                else root / ".pi" / "extensions" / "dinoguard.js")
    if harness == "openclaw":
        return paths.home() / "integrations" / "openclaw" / "index.js"
    if harness == "goose":
        base = _home("DG_GOOSE_HOME", ".agents") if user else root / ".agents"
        return base / "plugins" / "dinoguard" / "hooks" / "hooks.json"
    raise ValueError(f"no installer for harness {harness!r}")


def _goose_hooks() -> bytes:
    from .goose import HOOK_EVENTS
    cmd = hook_command("goose")
    hooks = {}
    for ev in HOOK_EVENTS:
        handler = {"command": f"{cmd} {ev}"}
        grp = {"hooks": [handler]}
        if ev in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            grp = {"matcher": ".*", **grp}             # a bare "*" is invalid in Goose and silently skipped
        if ev == "PreToolUse":
            grp["on_failure"] = "block"                # fail closed on crash / timeout / no decision
        hooks[ev] = [grp]
    return (json.dumps({"hooks": hooks}, indent=2) + "\n").encode("utf-8")


def _hooks_doc(settings: dict, harness: str, scope: str) -> dict:
    """Where the event map lives: Devin user config + Deep Agents nest it under "hooks"; the rest are flat."""
    if (harness == "devin" and scope == "user") or harness == "deepagents":
        return settings.setdefault("hooks", {})
    return settings


def _strip_groups(hooks: dict) -> None:
    for ev in list(hooks):
        groups = hooks[ev] if isinstance(hooks[ev], list) else []
        kept = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                hs = [h for h in g["hooks"] if not (isinstance(h, dict) and is_ours(h))]
                if hs:
                    kept.append({**g, "hooks": hs})
            elif not (isinstance(g, dict) and is_ours(g)):
                kept.append(g)
        if kept:
            hooks[ev] = kept
        elif isinstance(hooks[ev], list):
            del hooks[ev]


def strip(settings: dict, harness: str, scope: str) -> dict:
    if harness == "antigravity":
        settings.pop("dinoguard", None)
        return settings
    nested = (harness == "devin" and scope == "user") or harness == "deepagents"
    hooks = settings.get("hooks") if nested else settings
    if isinstance(hooks, dict):
        _strip_groups(hooks)
        if nested and not hooks:
            settings.pop("hooks", None)
    return settings


def _bridge_js(harness: str) -> str:
    from .plugin_bridge import OPENCLAW_PLUGIN, PI_EXTENSION
    cmd = hook_command(harness)
    if "-m dinoguard" in cmd:
        argv = [sys.executable.replace("\\", "/"), "-m", "dinoguard.hook"]
    else:
        argv = [cmd.rsplit(" ", 1)[0].strip('"')]
    return (PI_EXTENSION if harness == "pi" else OPENCLAW_PLUGIN).replace("__HOOK__", json.dumps(argv))


def render(harness: str, original: bytes | None, scope: str = "user") -> bytes:
    if harness == "goose":
        return _goose_hooks()
    if harness in FILE_ONLY:
        return _bridge_js(harness).encode("utf-8")
    cmd = hook_command(harness)
    settings = json.loads(original.decode("utf-8-sig")) if original and original.strip() else {}
    settings = strip(settings, harness, scope)
    if harness == "antigravity":
        h = lambda ev: {"type": "command", "command": f"{cmd} {ev}", "timeout": 30}  # noqa: E731
        settings["dinoguard"] = {"enabled": True,
                                 "PreToolUse": [{"matcher": "*", "hooks": [h("PreToolUse")]}],
                                 "PostToolUse": [{"matcher": "*", "hooks": [h("PostToolUse")]}],
                                 **{ev: [h(ev)] for ev in ("PreInvocation", "PostInvocation", "Stop", "SessionStart")}}
        return (json.dumps(settings, indent=2) + "\n").encode("utf-8")
    events = {"devin": DEVIN_EVENTS, "droid": DROID_EVENTS, "deepagents": DEEPAGENTS_EVENTS}[harness]
    hooks = _hooks_doc(settings, harness, scope)
    for ev in events:
        handler = {"type": "command", "command": cmd, "timeout": 30 if harness != "deepagents" else 180}
        if harness == "droid":
            handler["onFailure"] = "block"          # fail closed if the hook crashes or times out
        grp = {"hooks": [handler]}
        if ev in TOOL_EVENTS:
            grp = {"matcher": "*" if harness == "droid" else ("" if harness == "devin" else ".*"), **grp}
        hooks.setdefault(ev, []).append(grp)
    return (json.dumps(settings, indent=2) + "\n").encode("utf-8")


def pre_check(harness: str, scope: str, cwd: str) -> None:
    if harness == "devin":
        from .install import wiring
        if wiring("user", cwd).get("present") and not os.environ.get("DG_DEVIN_FORCE"):
            raise ValueError("Devin also loads your Claude Code hooks (read_config_from.claude is on by default), and "
                             "DinoGuard already recognises Devin calls coming through them. Installing Devin hooks "
                             "too would check every action twice. Either keep it as is, or turn off "
                             "read_config_from.claude in Devin and rerun with DG_DEVIN_FORCE=1.")


def after_install(harness: str, t: Path) -> None:
    if harness == "goose":
        from ..version import __version__
        (t.parent.parent / "plugin.json").write_text(json.dumps(
            {"name": "dinoguard", "version": __version__,
             "description": "DinoGuard guardrails: checks every tool call before it runs."}, indent=2), encoding="utf-8")
    if harness == "openclaw":
        from .plugin_bridge import OPENCLAW_MANIFEST, OPENCLAW_PACKAGE
        (t.parent / "openclaw.plugin.json").write_text(json.dumps(OPENCLAW_MANIFEST, indent=2), encoding="utf-8")
        (t.parent / "package.json").write_text(json.dumps(OPENCLAW_PACKAGE, indent=2), encoding="utf-8")


def after_uninstall(harness: str, t: Path) -> None:
    if harness == "goose":
        (t.parent.parent / "plugin.json").unlink(missing_ok=True)
        for d in (t.parent, t.parent.parent):
            try:
                d.rmdir()
            except OSError:
                pass
    if harness == "openclaw":
        for name in ("openclaw.plugin.json", "package.json"):
            (t.parent / name).unlink(missing_ok=True)
        try:
            t.parent.rmdir()
        except OSError:
            pass


NOTES = {
    "devin": "Devin's file-path field names aren't documented yet; check `dinoguard doctor` for hook drift.",
    "droid": "Hooks use onFailure=block, so a DinoGuard crash blocks the tool (fail closed).",
    "deepagents": "Deep Agents Code fails OPEN when a hook errors or times out; it has no fail-closed option. "
                  "Project hooks need workspace trust (--trust-project-hooks in CI).",
    "antigravity": "Antigravity runs hooks only in the agy CLI (reported, not confirmed for the IDE/App).",
    "pi": "Project extensions load only after you trust the project in Pi.",
    "openclaw": "Now run: openclaw plugins install --link \"{dir}\" && openclaw plugins enable dinoguard",
    "goose": "PreToolUse uses on_failure=block (fail closed); Stop hooks always fail open in Goose.",
}


def note(harness: str, t: Path) -> str:
    return NOTES.get(harness, "").replace("{dir}", str(t.parent))
