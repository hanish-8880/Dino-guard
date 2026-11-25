"""phone-approval: hold risky actions until a human answers YES on WhatsApp/Telegram."""
from __future__ import annotations

import re

from ...engine.decisions import allow, ask_human
from ...engine.registry import builtin_policy
from ._common import bash_cmd, git_commands

DEFAULT_PATTERNS = [
    r"\bterraform\s+(?:apply|destroy)\b",
    r"(?i)\b(?:psql|mysql|mongosh?|redis-cli|sqlcmd)\b[^\n]*\bprod",
    r"(?i)DATABASE_URL=[^\s]*prod",
]


@builtin_policy("phone-approval", category="approval", default_enabled=False,
                params={"patterns": DEFAULT_PATTERNS, "push_main": True, "protected": ["main", "master"],
                        "timeout_s": 120},
                description="Hold matching actions and ask the human on their phone. YES allows; NO or timeout denies.")
def phone_approval(ctx):
    if ctx.event_type != "PreToolUse" or ctx.tool_name != "Bash":
        return allow()
    cmd = bash_cmd(ctx)
    timeout = int(ctx.params.get("timeout_s", 120))
    for pat in ctx.params.get("patterns") or DEFAULT_PATTERNS:
        try:
            if re.search(pat, cmd):
                return ask_human(f"Needs human approval (matched `{pat}`): {cmd[:200]}", timeout)
        except re.error:
            continue
    if ctx.params.get("push_main", True):
        prot = set(ctx.params.get("protected") or ["main", "master"])
        for g in git_commands(ctx, "push"):
            pos = [w for w in g[2:] if not w.startswith("-")]
            targets = {r.lstrip("+").split(":")[-1].replace("refs/heads/", "") for r in pos[1:]}
            branch = None
            if not pos[1:]:
                branch = ctx.probe.branch(ctx.cwd) if ctx.probe else None
            if targets & prot or branch in prot:
                return ask_human(f"Needs human approval: push to {sorted(targets & prot) or [branch]}", timeout)
    return allow()
