"""Test helpers: build canonical events, fake probes, run single policies."""
from __future__ import annotations

import os

from dinoguard.engine.context import History, PolicyContext, TokenStats
from dinoguard.engine.events import CanonicalEvent, make_tool
from dinoguard.engine.modes import effective
from dinoguard.engine.runner import call_with_deadline


class FakeProbe:
    def __init__(self, branch=None, git=None, gh=None, in_repo=True):
        self._branch = branch
        self._git = dict(git or {})
        self._gh = dict(gh or {})
        if in_repo:
            self._git.setdefault("rev-parse --is-inside-work-tree", (0, "true"))
        if branch:
            self._git.setdefault("rev-parse --abbrev-ref HEAD", (0, branch))

    def git(self, cwd, *args):
        return self._git.get(" ".join(args), (1, ""))

    def gh(self, cwd, *args):
        return self._gh.get(" ".join(args), (1, ""))

    def branch(self, cwd):
        return self._branch

    def exists(self, p):
        return os.path.exists(p)

    def listdir(self, p):
        try:
            return os.listdir(p)
        except OSError:
            return []


def event(tool=None, inp=None, ev="PreToolUse", cwd="/work/proj", output=None, prompt=None, error=None,
          output_chars=None, session="s1", extra=None) -> CanonicalEvent:
    return CanonicalEvent(
        event=ev, harness="test", session={"id": session, "cwd": cwd},
        tool=make_tool(tool, inp) if tool else None, prompt=prompt, output=output,
        output_chars=output_chars if output_chars is not None else len(output or ""), error=error,
        extra=extra or {})


def bash(cmd, **kw) -> CanonicalEvent:
    return event("Bash", {"command": cmd}, **kw)


def run_policy(p, ev, history=None, params=None, probe=None, tokens=None, cfg=None):
    eff = effective(p, cfg or {})
    prm = dict(eff.params)
    prm.update(params or {})
    ctx = PolicyContext(event=ev, params=prm, history=history or History(), tokens=tokens or TokenStats(),
                        probe=probe or FakeProbe(), policy_name=p.name)
    if not p.matches(ev.event, ev.tool_name):
        from dinoguard.engine.decisions import allow
        return allow()
    d = call_with_deadline(p.fn, ctx, 10)
    from dinoguard.engine.decisions import allow
    return d or allow()
