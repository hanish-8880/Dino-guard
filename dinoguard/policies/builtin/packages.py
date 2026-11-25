"""Packages and system hygiene."""
from __future__ import annotations

import os
import re

from ...engine.decisions import allow, deny, instruct
from ...engine.registry import builtin_policy
from ._common import segs


def _pos(a):
    return [w for w in a[1:] if not w.startswith("-")]


@builtin_policy("no-package-publish", category="packages", default_enabled=False,
                description="Deny publishing packages (npm/pypi/cargo/gem publish).")
def no_package_publish(ctx):
    for s in segs(ctx):
        a, name = s.argv, s.cmd
        p = _pos(a)
        hit = (
            (name in ("npm", "yarn", "pnpm", "bun") and p[:1] == ["publish"])
            or (name in ("cargo", "poetry", "uv", "flit", "hatch", "gem") and p[:1] in (["publish"], ["push"]))
            or (name == "twine" and p[:1] == ["upload"])
            or (name.startswith("python") and "twine" in a and "upload" in a)
            or (name == "dotnet" and p[:2] == ["nuget", "push"])
            or (name == "gh" and p[:2] == ["release", "create"])
        )
        if hit:
            return deny("Publishing a package is irreversible and public. Prepare the release (version bump, "
                        "changelog, build) and ask the user to publish it themselves.")
    return allow()


@builtin_policy("no-global-install", category="packages", default_enabled=False, authority="reviewable",
                reviewed_by=["on-task"], description="Deny global/system-wide package installs.")
def no_global_install(ctx):
    for s in segs(ctx):
        a, name = s.argv, s.cmd
        p = _pos(a)
        flags = [w for w in a[1:] if w.startswith("-")]
        hit = (
            (name in ("npm", "pnpm", "bun") and p[:1] and p[0] in ("i", "install", "add") and
             ("-g" in flags or "--global" in flags))
            or (name == "yarn" and p[:2] == ["global", "add"])
            or (name in ("pip", "pip3") and p[:1] == ["install"] and
                ("--user" in flags or "--break-system-packages" in flags))
            or (name in ("apt", "apt-get", "yum", "dnf", "pacman", "brew", "choco", "winget", "scoop", "snap")
                and p[:1] and p[0] in ("install", "add", "-S"))
            or (name == "gem" and p[:1] == ["install"])
        )
        if hit:
            return deny("Global installs change the user's machine outside this project. Install as a project "
                        "dependency (package.json / pyproject / requirements) or use npx / pipx run / uvx, or ask "
                        "the user to install it.")
    return allow()


_LOCKS = [("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"), ("bun.lock", "bun"),
          ("uv.lock", "uv"), ("poetry.lock", "poetry"), ("Pipfile.lock", "pipenv"), ("package-lock.json", "npm")]
_JS = {"npm", "yarn", "pnpm", "bun"}
_PY = {"pip", "pip3", "uv", "poetry", "pipenv"}


@builtin_policy("use-repo-package-manager", category="packages", default_enabled=False,
                description="Instruct the agent to use the package manager the lockfile says (pnpm, yarn, uv, poetry...).")
def use_repo_package_manager(ctx):
    cwd = ctx.cwd
    present = [pm for f, pm in _LOCKS if os.path.exists(os.path.join(cwd, f))]
    if not present:
        return allow()
    for s in segs(ctx):
        name = s.cmd
        if name == "npx":
            continue
        family = _JS if name in _JS else _PY if name in _PY else None
        if not family:
            continue
        expected = next((pm for pm in present if pm in family), None)
        if expected and expected != name and not (expected == "uv" and name == "uv"):
            if expected == "npm" and name == "npm":
                continue
            p = _pos(s.argv)
            if p[:1] and p[0] in ("install", "i", "add", "remove", "uninstall", "run", "ci", "update", "sync"):
                return instruct(f"This repo uses {expected} (lockfile present), but you ran `{name}`. Use "
                                f"`{expected}` instead so the lockfile stays consistent.")
    return allow()


@builtin_policy("warn-large-write", category="packages", default_enabled=False, params={"max_kb": 100},
                description="Instruct when a single Write is larger than max_kb.")
def warn_large_write(ctx):
    if ctx.tool_name != "Write":
        return allow()
    size = len(str(ctx.tool_input.get("content") or "").encode("utf-8"))
    limit = int(ctx.params.get("max_kb", 100)) * 1024
    if size > limit:
        return instruct(f"You are writing {size // 1024} KB in one file. Large generated files are hard to review; "
                        "split it, generate it with a script that is committed instead, or confirm with the user.")
    return allow()


_BG = re.compile(r"(?i)\b(?:nohup|disown|setsid)\b|\bscreen\s+-d?m|\btmux\s+new(?:-session)?\b[^\n]*-d\b|\bstart\s+/b\b|\bstart-process\b")


@builtin_policy("warn-background-process", category="packages", default_enabled=False, authority="reviewable",
                reviewed_by=["on-task"], description="Instruct when a command starts a detached background process.")
def warn_background_process(ctx):
    if ctx.tool_name != "Bash" or ctx.tool_input.get("run_in_background"):
        return allow()
    cmd = str(ctx.tool_input.get("command") or "")
    if _BG.search(cmd) or any(s.sep_after == "&" for s in segs(ctx)):
        return instruct("This starts a detached background process that will outlive your command and is easy to "
                        "forget. Prefer the harness's managed background option, and tell the user how to stop it.")
    return allow()
