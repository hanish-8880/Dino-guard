"""Install/uninstall hooks for Cursor, Gemini CLI, Copilot, Codex and OpenCode.

Same guarantees as the Claude installer: back up, merge, and restore the exact original bytes on uninstall
when nothing else changed since install.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

from .. import paths
from ..util import now_iso
from .install import _load_state, _save_state, _sha, hook_command, is_ours

CURSOR_EVENTS = ["preToolUse", "postToolUse", "postToolUseFailure", "beforeShellExecution", "beforeMCPExecution",
                 "beforeReadFile", "afterFileEdit", "beforeSubmitPrompt", "stop", "subagentStart", "subagentStop",
                 "sessionStart", "sessionEnd", "preCompact"]
GEMINI_EVENTS = ["BeforeTool", "AfterTool", "BeforeAgent", "AfterAgent", "SessionStart", "SessionEnd", "PreCompress",
                 "Notification"]
COPILOT_EVENTS = ["preToolUse", "postToolUse", "postToolUseFailure", "userPromptSubmitted", "sessionStart",
                  "sessionEnd", "agentStop", "subagentStart", "subagentStop", "preCompact"]
CODEX_EVENTS = ["PreToolUse", "PostToolUse", "PermissionRequest", "UserPromptSubmit", "Stop", "SubagentStop",
                "SessionStart", "SessionEnd", "PreCompact"]


def _home(env: str, default: str) -> Path:
    return Path(os.environ[env]) if os.environ.get(env) else Path.home() / default


def target(harness: str, scope: str, cwd: str) -> Path:
    from . import more_install
    if harness in more_install.MORE:
        return more_install.target(harness, scope, cwd)
    root = Path(cwd)
    if harness == "cursor":
        return (root / ".cursor" / "hooks.json") if scope != "user" else _home("DG_CURSOR_HOME", ".cursor") / "hooks.json"
    if harness == "gemini":
        return (root / ".gemini" / "settings.json") if scope != "user" else _home("DG_GEMINI_HOME", ".gemini") / "settings.json"
    if harness == "copilot":
        return (root / ".github" / "hooks" / "dinoguard.json") if scope != "user" else \
            _home("DG_COPILOT_HOME", ".copilot") / "hooks" / "dinoguard.json"
    if harness == "codex":
        return (root / ".codex" / "hooks.json") if scope != "user" else _home("DG_CODEX_HOME", ".codex") / "hooks.json"
    if harness == "opencode":
        return (root / ".opencode" / "plugins" / "dinoguard.js") if scope != "user" else \
            _home("DG_OPENCODE_HOME", ".config/opencode") / "plugins" / "dinoguard.js"
    raise ValueError(f"no installer for harness {harness!r}")


def _strip(settings: dict, events_key: str = "hooks") -> dict:
    hooks = settings.get(events_key) or {}
    for ev in list(hooks):
        kept = []
        for g in hooks[ev] if isinstance(hooks[ev], list) else []:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                hs = [h for h in g["hooks"] if not (isinstance(h, dict) and is_ours(h))]
                if hs:
                    kept.append({**g, "hooks": hs})
            elif isinstance(g, dict) and any(MARK in str(g.get(k, "")).lower() for k in ("command", "bash", "powershell")):
                continue
            else:
                kept.append(g)
        if kept:
            hooks[ev] = kept
        else:
            del hooks[ev]
    if events_key in settings and not hooks:
        del settings[events_key]
    return settings


MARK = "dinoguard"


def render(harness: str, original: bytes | None) -> bytes:
    cmd = hook_command(harness)
    if harness == "opencode":
        exe = cmd.split(" ")[0].strip('"') if "-m dinoguard" not in cmd else sys.executable.replace("\\", "/")
        argv = [exe] if "-m dinoguard" not in cmd else [exe, "-m", "dinoguard.hook"]
        from .opencode import PLUGIN_JS
        return PLUGIN_JS.replace("__HOOK__", json.dumps(argv)).encode("utf-8")
    if harness == "copilot":
        doc = {"version": 1, "hooks": {ev: [{"type": "command", "bash": f"{cmd} {ev}", "powershell": f"{cmd} {ev}",
                                             "timeoutSec": 180}] for ev in COPILOT_EVENTS}}
        return (json.dumps(doc, indent=2) + "\n").encode()
    settings = json.loads(original.decode("utf-8")) if original and original.strip() else {}
    settings = _strip(settings)
    if harness == "cursor":
        settings.setdefault("version", 1)
        hooks = settings.setdefault("hooks", {})
        for ev in CURSOR_EVENTS:
            hooks.setdefault(ev, []).append({"command": cmd, "timeout": 180, "failClosed": True})
    elif harness == "gemini":
        hooks = settings.setdefault("hooks", {})
        for ev in GEMINI_EVENTS:
            grp = {"hooks": [{"name": "dinoguard", "type": "command", "command": cmd, "timeout": 180000}]}
            if ev in ("BeforeTool", "AfterTool"):
                grp = {"matcher": ".*", **grp}
            hooks.setdefault(ev, []).append(grp)
    elif harness == "codex":
        hooks = settings.setdefault("hooks", {})
        for ev in CODEX_EVENTS:
            grp = {"hooks": [{"type": "command", "command": cmd, "timeout": 180}]}
            if ev in ("PreToolUse", "PostToolUse", "PermissionRequest"):
                grp = {"matcher": ".*", **grp}
            hooks.setdefault(ev, []).append(grp)
    return (json.dumps(settings, indent=2) + "\n").encode("utf-8")


def _strip_cursor_flat(settings: dict) -> dict:
    hooks = settings.get("hooks") or {}
    for ev in list(hooks):
        hooks[ev] = [h for h in hooks[ev] if not (isinstance(h, dict) and is_ours(h))]
        if not hooks[ev]:
            del hooks[ev]
    if "hooks" in settings and not hooks:
        del settings["hooks"]
    return settings


def install_other(harness: str, scope: str = "user", cwd: str | None = None) -> dict:
    from . import more_install
    cwd = cwd or os.getcwd()
    if harness == "hermes":
        from .hermes import SNIPPET
        return {"settings": "(manual: Hermes profile config.yaml)", "command": hook_command("hermes"),
                "note": "Hermes config.yaml is yours, so DinoGuard doesn't rewrite it. Add this:\n"
                        + SNIPPET.replace("{cmd}", hook_command("hermes").replace('"', '\\"'))}
    more = harness in more_install.MORE
    if more:
        more_install.pre_check(harness, scope, cwd)
    t = target(harness, scope, cwd)
    original = t.read_bytes() if t.exists() else None
    if original is not None:
        (paths.backups_dir() / f"{harness}-{scope}-{t.name}-{now_iso().replace(':', '-')}").write_bytes(original)
    if harness == "cursor" and original:
        tmp = json.loads(original.decode() or "{}")
        original_clean = json.dumps(_strip_cursor_flat(tmp)).encode()
        out = render(harness, original_clean)
    elif more:
        out = more_install.render(harness, original, scope)
    else:
        out = render(harness, original)
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_bytes(out)
    if more:
        more_install.after_install(harness, t)
    st = _load_state()
    prev = st.get(str(t))
    orig_b64 = prev["original"] if prev and prev.get("written_sha") == (_sha(original) if original else None) \
        else (base64.b64encode(original).decode() if original is not None else None)
    st[str(t)] = {"harness": harness, "scope": scope, "original": orig_b64, "written_sha": _sha(out),
                  "installed_at": now_iso()}
    _save_state(st)
    note = ""
    if harness == "codex":
        note = ("Open Codex and trust the new hooks once with /hooks (Codex won't run unreviewed hooks; editing them "
                "resets the trust). Hooks are on by default unless [features] hooks = false.")
    if harness == "gemini":
        note = "Gemini CLI may be replaced by Antigravity CLI for some tiers; check your CLI supports hooks."
    if more:
        note = more_install.note(harness, t)
    return {"settings": str(t), "command": hook_command(harness), "note": note}


def uninstall_other(harness: str, scope: str = "user", cwd: str | None = None) -> dict:
    from . import more_install
    if harness == "hermes":
        return {"settings": "(manual)", "action": "remove the dinoguard-hook entries from your Hermes config.yaml"}
    t = target(harness, scope, cwd or os.getcwd())
    if harness in more_install.MORE:
        res = _uninstall_file(harness, scope, t)
        more_install.after_uninstall(harness, t)
        return res
    return _uninstall_file(harness, scope, t)


def _uninstall_file(harness: str, scope: str, t: Path) -> dict:
    from . import more_install
    st = _load_state()
    entry = st.pop(str(t), None)
    _save_state(st)
    if not t.exists():
        return {"settings": str(t), "action": "nothing to remove"}
    cur = t.read_bytes()
    if entry and entry.get("written_sha") == _sha(cur):
        if entry.get("original") is None:
            t.unlink()
            return {"settings": str(t), "action": "removed file created by install"}
        t.write_bytes(base64.b64decode(entry["original"]))
        return {"settings": str(t), "action": "restored original byte-for-byte"}
    if harness in ("opencode", "copilot") + more_install.FILE_ONLY:
        t.unlink()
        return {"settings": str(t), "action": "removed DinoGuard file"}
    data = json.loads(cur.decode("utf-8-sig") or "{}")
    if harness in more_install.MORE:
        data = more_install.strip(data, harness, scope)
    else:
        data = _strip_cursor_flat(data) if harness == "cursor" else _strip(data)
    t.write_bytes((json.dumps(data, indent=2) + "\n").encode())
    return {"settings": str(t), "action": "file changed since install; removed only DinoGuard hooks"}
