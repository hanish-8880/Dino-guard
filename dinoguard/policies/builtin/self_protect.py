"""guard-self-protect: the agent may not disable, uninstall or tamper with DinoGuard."""
from __future__ import annotations

import os
import re

from ... import paths
from ...engine.decisions import allow, deny
from ...engine.registry import builtin_policy
from ...util import norm_path, path_key
from . import _shell
from ._common import bash_cmd, file_path, segs

_DG = r"(?:dinoguard(?:\.exe)?|python[0-9.]*(?:\.exe)?\s+-m\s+dinoguard(?:\.cli(?:\.main)?)?|py\s+-m\s+dinoguard)"
_MUTATING = re.compile(
    _DG + r"\s+(?:uninstall|install|pause|approve|publish|channel|daemon\s+(?:stop|restart|kill)"
    r"|polic(?:y|ies)\s+(?:enable|disable|observe|enforce|add|remove|set|deploy|rollback|publish)"
    r"|reviewer\s+setup|eval\s+deploy|alerts\s+(?:disable|delete))\b", re.I)
_KILL = re.compile(r"(?i)\b(?:pkill|killall|kill|taskkill|stop-process|spps)\b[^\n;|&]*dinoguard")
_KILL_PID = re.compile(r"(?i)\bkill\b.*dinoguardd\.pid")
_PKG_REMOVE = re.compile(r"(?i)\b(?:pip3?|pipx|uv\s+pip|uv\s+tool|python[0-9.]*\s+-m\s+pip)\s+uninstall\b[^\n;|&]*\bdinoguard\b")
_ENV_TAMPER = re.compile(r"(?i)\b(?:export\s+|set\s+|setx\s+|\$env:)(?:DINOGUARD_HOME|DG_CLAUDE_SETTINGS|DG_CLAUDE_HOME)\b")
_DISABLE_HOOKS = re.compile(r'"?disableAllHooks"?\s*:\s*true', re.I)
# The local dashboard can change policies; the agent must not drive its write API.
_DASH = re.compile(r"(?i)(?:localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0):8030")
_HTTP_WRITE = re.compile(r"(?i)(?:-X\s*|--request\s+|-Method\s+)(?:POST|PUT|PATCH|DELETE)\b|\s(?:-d|--data\S*|--json|-F|--form)\s"
                         r"|\.(?:post|put|patch|delete)\(|Invoke-RestMethod|\bhttp\s+(?:POST|PUT|PATCH|DELETE)\b")
_READ_ONLY = {"cat", "ls", "dir", "head", "tail", "less", "more", "grep", "rg", "wc", "type", "get-content",
              "gc", "stat", "file", "find", "tree", "bat", "jq", "echo", "test", "get-childitem", "gci",
              "select-string", "sls", "diff", "cmp", "md5sum", "sha256sum", "git", "realpath", "readlink"}

REASON = ("This action would disable, bypass or tamper with DinoGuard (the safety guard for this session). "
          "That is never allowed for the agent. If the guard is in the way, explain why to the user and let "
          "them change it from their own terminal.")


def _protected_paths(cwd: str) -> list[str]:
    from ...util import norm_path as n
    out = [n(str(paths.home())), n(os.path.expanduser("~/.dinoguard"))]
    if cwd:
        out.append(n(os.path.join(cwd, ".dinoguard")))
    return out


def _settings_like(p: str) -> bool:
    k = path_key(p)
    return bool(re.search(r"(^|/)\.claude/settings(\.local)?\.json$", k)) or k.endswith("managed-settings.json") \
        or (os.environ.get("DG_CLAUDE_SETTINGS") and k == path_key(os.environ["DG_CLAUDE_SETTINGS"]))


def _in_protected(p: str, cwd: str) -> bool:
    k = path_key(norm_path(p, cwd))
    if "/.dinoguard/" in k + "/" or k.startswith(".dinoguard/") or k == ".dinoguard":
        return True
    return any(k == path_key(x) or k.startswith(path_key(x) + "/") for x in _protected_paths(cwd))


def _mentions_protected(text: str, cwd: str) -> bool:
    t = text.replace("\\", "/").lower()
    if ".dinoguard" in t or "dinoguardd" in t:
        return True
    home = norm_path(str(paths.home())).lower()
    return home in t


def _check_bash(ctx) -> str | None:
    cmd = bash_cmd(ctx)
    if not cmd:
        return None
    flat = " ".join(cmd.split())
    for rx in (_MUTATING, _KILL, _KILL_PID, _PKG_REMOVE, _ENV_TAMPER):
        if rx.search(flat):
            return REASON
    if _DISABLE_HOOKS.search(cmd) and re.search(r"(?i)settings", cmd):
        return REASON
    if _DASH.search(cmd) and _HTTP_WRITE.search(cmd + " "):
        return REASON
    for s in segs(ctx):
        name = s.cmd
        writes = _shell.written_files(s)
        if any(_in_protected(w, ctx.cwd) or _settings_like(norm_path(w, ctx.cwd)) for w in writes):
            return REASON
        if name in _READ_ONLY and name != "git":
            continue
        if name == "git" and not re.search(r"(?i)\b(rm|mv|checkout|restore|clean)\b", s.text):
            continue
        if _mentions_protected(s.text, ctx.cwd):
            return REASON
        if re.search(r"(?i)\.claude[/\\]+settings(\.local)?\.json", s.text):
            return REASON
    return None


def _check_file(ctx) -> str | None:
    p = file_path(ctx)
    if not p:
        return None
    full = norm_path(p, ctx.cwd)
    if _in_protected(full, ctx.cwd):
        return REASON
    if not _settings_like(full):
        return None
    inp = ctx.tool_input
    if ctx.tool_name == "Edit":
        old, new = str(inp.get("old_string") or ""), str(inp.get("new_string") or "")
        if "dinoguard" in old.lower() or _DISABLE_HOOKS.search(new) or '"hooks"' in old:
            return REASON
        return None
    # Write: the new file must keep every DinoGuard hook the old one had.
    new = str(inp.get("content") or "")
    try:
        with open(full, encoding="utf-8") as f:
            old = f.read()
    except OSError:
        old = ""
    if _DISABLE_HOOKS.search(new):
        return REASON
    if old.lower().count("dinoguard") > new.lower().count("dinoguard"):
        return REASON
    return None


@builtin_policy("guard-self-protect", category="self-protection", always_on=True,
                events=["PreToolUse", "PermissionRequest"],
                description="Deny agent actions that disable, uninstall or tamper with DinoGuard.")
def guard_self_protect(ctx):
    if ctx.event_type not in ("PreToolUse", "PermissionRequest"):
        return allow()
    reason = None
    if ctx.tool_name == "Bash":
        reason = _check_bash(ctx)
    elif ctx.tool_name in ("Write", "Edit"):
        reason = _check_file(ctx)
    elif (ctx.tool_name or "").startswith("MCP:"):
        text = _shell.text_of_input(ctx.tool_input)
        if _MUTATING.search(text) or _KILL.search(text):
            reason = REASON
    return deny(reason) if reason else allow()
