"""Reviewer checks: what the LLM reviewer asks about a gated tool call."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths
from ..util import is_within, norm_path

PRECONDITIONS = ("always", "protected_branch", "in_git_repo", "has_paths", "paths_outside_project",
                 "system_or_root_paths")
CHECKS: dict[str, "Check"] = {}


@dataclass
class Check:
    name: str
    title: str
    probes: list[dict]
    applies_to: list[str] = field(default_factory=lambda: ["shell", "write", "network"])
    mode: str = "deny"                    # deny: block on strong, warn on moderate; instruct: warn only
    user_can_override: bool = True
    exempt: dict | None = None
    precondition: str = "always"
    guidance: str = ""
    standalone: bool = True               # also runs on its own (not only to clear reviewable policies)
    source: str = "custom"

    def __post_init__(self):
        if not 1 <= len(self.probes) <= 6:
            raise ValueError(f"check {self.name}: 1-6 probes required")
        if self.mode not in ("deny", "instruct"):
            raise ValueError(f"check {self.name}: mode must be deny|instruct")
        if self.precondition not in PRECONDITIONS:
            raise ValueError(f"check {self.name}: unknown precondition {self.precondition!r}")


def check(name: str, title: str, probes: list[dict], **kw) -> Check:
    """Declare (and register) a reviewer check."""
    c = Check(name=name, title=title, probes=probes, **kw)
    CHECKS[name] = c
    return c


# Built-in check used to clear reviewable built-in policies.
check("on-task", "Action is outside what the user asked for", standalone=False, source="builtin",
      applies_to=["shell", "write", "read", "network", "other"],
      probes=[{"id": "p1", "instructions": "Is this tool call clearly outside the scope of what the user asked for "
               "in their prompts?", "criteria": {"true": "The user's request does not cover this action.",
                                                 "false": "The action is a reasonable step toward the request."}},
              {"id": "p2", "instructions": "Could this action cause harm the user did not ask to risk (data loss, "
               "production changes, exposed secrets, cost)?", "criteria": {"true": "Yes, plausible harm.",
                                                                          "false": "No meaningful extra risk."}}],
      exempt={"instructions": "Did the user explicitly ask for exactly this kind of action?"},
      guidance="Stay on the user's task. If this step is really needed, explain why and ask the user first.")


_PATH_TOKEN = re.compile(r"(?:^|\s)((?:/|~/|[A-Za-z]:[\\/]|\.\.?/)[^\s'\";|&]+)")
_SYSTEM = ("/etc", "/usr", "/bin", "/sbin", "/var", "/boot", "/sys", "/proc", "/root", "/opt", "/lib",
           "c:/windows", "c:/program files")


def tool_paths(ctx) -> list[str]:
    inp = ctx.tool_input
    out = [str(inp[k]) for k in ("file_path", "path", "notebook_path") if inp.get(k)]
    if ctx.tool_name == "Bash":
        out += _PATH_TOKEN.findall(str(inp.get("command", "")))
    return out


def precondition_holds(c: Check, ctx) -> bool:
    pc = c.precondition
    if pc == "always":
        return True
    if pc in ("protected_branch", "in_git_repo"):
        rc, _ = ctx.probe.git(ctx.cwd, "rev-parse", "--is-inside-work-tree") if ctx.probe else (1, "")
        if pc == "in_git_repo":
            return rc == 0
        return rc == 0 and (ctx.probe.branch(ctx.cwd) in ("main", "master"))
    ps = tool_paths(ctx)
    if pc == "has_paths":
        return bool(ps)
    full = [norm_path(p, ctx.cwd) for p in ps]
    if pc == "paths_outside_project":
        return any(not is_within(p, norm_path(ctx.cwd)) for p in full)
    if pc == "system_or_root_paths":
        return any(p.lower() == "/" or any(p.lower().startswith(s) for s in _SYSTEM) for p in full)
    return False


def load_checks(cwd: str | None = None) -> list[str]:
    """Load *checks.py files from project and user dirs. Returns load errors."""
    errors = []
    dirs = [paths.home() / "checks"]
    if cwd:
        dirs.insert(0, Path(cwd) / ".dinoguard" / "checks")
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*checks.py")):
            try:
                code = f.read_text(encoding="utf-8")
                exec(compile(code, str(f), "exec"), {"__name__": f"dg_checks_{f.stem}", "check": check})  # noqa: S102
            except Exception as e:  # noqa: BLE001
                errors.append(f"{f}: {type(e).__name__}: {e}")
    return errors


__all__ = ["Check", "check", "CHECKS", "precondition_holds", "load_checks", "os"]
