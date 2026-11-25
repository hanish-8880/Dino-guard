"""Helpers shared by built-in policies."""
from __future__ import annotations

import os
import re

from ...util import norm_path
from . import _shell

FILE_TOOLS = ("Read", "Write", "Edit")
WRITE_TOOLS = ("Write", "Edit")


def bash_cmd(ctx) -> str:
    return str(ctx.tool_input.get("command") or "") if ctx.tool_name == "Bash" else ""


def segs(ctx) -> list[_shell.Segment]:
    return _shell.segments(bash_cmd(ctx))


def file_path(ctx) -> str:
    inp = ctx.tool_input
    return str(inp.get("file_path") or inp.get("path") or inp.get("notebook_path") or "")


def abs_path(ctx, p: str) -> str:
    return norm_path(p, ctx.cwd)


def basename(p: str) -> str:
    return os.path.basename(p.replace("\\", "/").rstrip("/"))


def written_content(ctx) -> str:
    inp = ctx.tool_input
    if ctx.tool_name == "Write":
        return str(inp.get("content") or "")
    if ctx.tool_name == "Edit":
        return str(inp.get("new_string") or "")
    return ""


def git_argv(seg: _shell.Segment) -> list[str] | None:
    """Return ['git', sub, *args] with global options removed, or None if not a git command."""
    a = seg.argv
    if not a or a[0] and _shell.cmd_name(a[0]) != "git":
        return None
    out, i = ["git"], 1
    while i < len(a):
        w = a[i]
        if w in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(a):
            i += 2
            continue
        if w.startswith("--git-dir=") or w.startswith("--work-tree=") or w in ("--no-pager", "-p", "--bare"):
            i += 1
            continue
        break
    return out + a[i:]


def git_commands(ctx, sub: str | None = None) -> list[list[str]]:
    out = []
    for s in segs(ctx):
        g = git_argv(s)
        if g and len(g) > 1 and (sub is None or g[1] == sub):
            out.append(g)
    return out


def word_re(*words: str) -> re.Pattern:
    return re.compile(r"(?i)\b(?:" + "|".join(re.escape(w) for w in words) + r")\b")


ENV_SAFE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".defaults")


def is_env_file(p: str) -> bool:
    b = basename(p).lower()
    if not (b == ".env" or b.startswith(".env.") or b.endswith(".env")):
        return False
    return not b.endswith(ENV_SAFE_SUFFIXES)
