"""AI failure modes: loops, hallucinated paths, goal drift, finished tasks, destructive shapes."""
from __future__ import annotations

import difflib
import os
import re

from ...engine.context import input_key
from ...engine.decisions import allow, deny, instruct
from ...engine.registry import builtin_policy
from ...util import norm_path
from ._common import bash_cmd, file_path, git_commands, segs

TEST_CMD = re.compile(r"(?i)\b(?:pytest|py\.test|tox|nox|(?:npm|yarn|pnpm|bun)\s+(?:run\s+)?test|jest|vitest|mocha"
                      r"|go\s+test|cargo\s+test|mvn\s+test|gradle\w*\s+test|dotnet\s+test|rspec|phpunit"
                      r"|make\s+test|ctest|python\s+-m\s+(?:pytest|unittest))\b")


@builtin_policy("loop-detected", category="ai-behaviour", params={"warn_at": 3, "stop_at": 5, "window": 10},
                description="Steer the agent out of repeated identical calls; stop and ask the human after N repeats.")
def loop_detected(ctx):
    if ctx.event_type != "PreToolUse":
        return allow()
    warn_at, stop_at = int(ctx.params.get("warn_at", 3)), int(ctx.params.get("stop_at", 5))
    window = int(ctx.params.get("window", 10))
    key = input_key(ctx.tool_name, ctx.tool_input)
    recent = ctx.history.tool_calls(window - 1)
    same = 1 + sum(1 for h in recent if input_key(h.get("tool_name"), h.get("input")) == key)
    failed = 0
    if ctx.tool_name == "Bash":
        cmd = bash_cmd(ctx)
        for h in ctx.history.last(window * 3):
            if h.get("event") in ("PostToolUseFailure", "PostToolUse") and h.get("error") and \
                    (h.get("input") or {}).get("command") == cmd:
                failed += 1
        failed += 1 if failed else 0
    n = max(same, failed)
    if n >= stop_at:
        return deny(f"You have made this exact call {n} times recently without progress. Stop, summarise what you "
                    "tried and what failed, and ask the user how to proceed.")
    if n >= warn_at:
        return instruct(f"You have repeated this exact call {n} times. Summarise your progress so far and try a "
                        "different approach instead of repeating it.")
    return allow()


def _suggest(missing: str, cwd: str) -> list[str]:
    parent = os.path.dirname(missing)
    while parent and not os.path.isdir(parent) and parent != os.path.dirname(parent):
        parent = os.path.dirname(parent)
    try:
        names = os.listdir(parent or cwd)
    except OSError:
        return []
    base = os.path.basename(missing)
    return [os.path.join(parent, n).replace("\\", "/") for n in difflib.get_close_matches(base, names, n=3, cutoff=0.5)]


@builtin_policy("tool-not-found", category="ai-behaviour", default_enabled=False,
                description="Deny calls on files/paths that do not exist and suggest the nearest real names.")
def tool_not_found(ctx):
    if ctx.event_type != "PreToolUse":
        return allow()
    p = ""
    if ctx.tool_name in ("Read", "Edit"):
        p = file_path(ctx)
    elif ctx.tool_name in ("Grep", "Glob"):
        p = str(ctx.tool_input.get("path") or "")
    if not p:
        return allow()
    full = norm_path(p, ctx.cwd)
    if os.path.exists(full):
        return allow()
    near = _suggest(full, ctx.cwd)
    hint = f" Did you mean: {', '.join(near)}?" if near else " List the directory (Glob/ls) to find the right name."
    return deny(f"{p} does not exist.{hint}")


_STOP = set("the a an and or to of in on for with this that it is be are was please can you me my we our make "
            "add fix use using into from about just some all any should would could need want".split())
DEFAULT_AREAS = ["payment", "billing", "invoice", "auth", "secret", "credential", "infra", "terraform", "deploy",
                 "migration", "prod", "kube", "k8s"]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9]{2,}", (text or "").lower()) if w not in _STOP}


@builtin_policy("goal-drift", category="ai-behaviour", default_enabled=False, params={"areas": DEFAULT_AREAS},
                description="Instruct when an action touches a sensitive area the user never mentioned.")
def goal_drift(ctx):
    if ctx.event_type != "PreToolUse" or not ctx.history.first_prompt:
        return allow()
    action = " ".join([file_path(ctx), bash_cmd(ctx)]).lower()
    if not action.strip():
        return allow()
    asked = set()
    for p in ctx.history.prompts or [ctx.history.first_prompt]:
        asked |= _words(p)
    for area in ctx.params.get("areas") or DEFAULT_AREAS:
        if area in action and not any(area in w or w in area for w in asked if len(w) >= 4):
            goal = ctx.history.first_prompt.strip().replace("\n", " ")[:200]
            return instruct(f"This action touches '{area}', which the user never mentioned. The task is: \"{goal}\". "
                            "Stay on that task, or ask the user before changing this area.")
    return allow()


_DONE_TESTS = re.compile(r"(?i)\b(make|get)\b.*\btests?\b.*\bpass|\bfix\b.*\b(failing|broken)\b.*\btests?\b|\btests?\b.*\bgreen\b")
_CREATE = re.compile(r"(?i)\b(?:create|add|write|generate)\b[^.\n]*?\b([\w./-]+\.\w{1,6})\b")


@builtin_policy("intent-complete", events=["PostToolUse"], category="ai-behaviour", default_enabled=False,
                description="Instruct the agent to report and stop once the requested outcome is met.")
def intent_complete(ctx):
    goal = ctx.history.latest_prompt or ""
    if not goal or ctx.event.error:
        return allow()
    if ctx.tool_name == "Bash" and TEST_CMD.search(bash_cmd(ctx)) and _DONE_TESTS.search(goal):
        out = (ctx.event.output or "").lower()
        if not re.search(r"\b(failed|error|failures?)\b", out.replace("0 failed", "")):
            return instruct("The tests now pass, which is what the user asked for. Report the result and the change "
                            "you made, then stop rather than continuing to edit.")
    if ctx.tool_name == "Write":
        m = _CREATE.search(goal)
        if m and os.path.basename(file_path(ctx)) == os.path.basename(m.group(1)):
            return instruct(f"{m.group(1)} now exists as requested. Report what you created and stop unless the user "
                            "asked for more.")
    return allow()


_STEER: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bgit\s+checkout\s+(?:--\s+)?\.(\s|$)"), "`git stash` (keeps the changes recoverable)"),
    (re.compile(r"\bgit\s+restore\s+(?:--\S+\s+)*\.(\s|$)"), "`git stash` or restore specific files by name"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "`git stash` or `git reset --soft`"),
    (re.compile(r"\bgit\s+branch\s+-D\b"), "`git branch -d` (refuses to delete unmerged work)"),
    (re.compile(r"\bgit\s+push\s+\S+\s+(?:--delete\s+|:)\S+"), "leaving the remote branch and telling the user"),
    (re.compile(r"\bfind\b[^\n]*\s-delete\b"), "listing the matches first (`find ... -print`) and deleting by name"),
    (re.compile(r"\bdocker\s+(?:system|volume|image)\s+prune\b"), "removing the specific containers/images by id"),
    (re.compile(r"\bdocker\s+volume\s+rm\b"), "keeping the volume and asking the user"),
    (re.compile(r"\btruncate\s+-s\s*0\b"), "editing the file or moving it aside"),
    (re.compile(r"\bchown\s+-R\b"), "changing ownership of only the files that need it"),
    (re.compile(r"\bkill\s+-9\s+-1\b"), "stopping the specific process by pid"),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\b"), "moving the directory aside (`mv dir dir.bak`)"),
    (re.compile(r"(?i)\bremove-item\b[^\n]*-recurse\b"), "moving the directory aside (`Move-Item`)"),
]


@builtin_policy("destructive-command", category="ai-behaviour", default_enabled=False,
                description="Catch-all for destructive command shapes; deny and suggest a safe alternative.")
def destructive_command(ctx):
    cmd = bash_cmd(ctx)
    if not cmd:
        return allow()
    for s in segs(ctx):
        for rx, alt in _STEER:
            if rx.search(s.text):
                return deny(f"`{s.text[:80]}` destroys work that may not be recoverable. Instead, use {alt}.")
    return allow()


__all__ = ["TEST_CMD", "git_commands"]
