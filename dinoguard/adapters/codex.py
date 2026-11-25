"""OpenAI Codex CLI hooks adapter.

Codex documents Claude-style lifecycle hooks (PreToolUse, PostToolUse, UserPromptSubmit, Stop, ...) in
~/.codex/hooks.json, enabled with `features.hooks = true`. The stdin payload and block format were NOT
documented on the pages read, so this adapter assumes the Claude-compatible shape. Marked unverified in
docs/adapters/codex.md - check https://developers.openai.com/codex/hooks before relying on it.
"""
from __future__ import annotations

import shlex

from .claude_code import ClaudeCodeAdapter


class CodexAdapter(ClaudeCodeAdapter):
    name = "codex"
    blocking_events = frozenset({"PreToolUse", "PermissionRequest", "UserPromptSubmit", "Stop", "SubagentStop",
                                 "PreCompact"})

    def to_canonical(self, raw):
        ev = super().to_canonical(raw)
        ev.harness = "codex"
        if ev.tool and ev.tool.get("native_name") in ("shell", "exec_command", "local_shell"):
            cmd = (raw.get("tool_input") or {}).get("command")
            ev.tool["name"], ev.tool["class"] = "Bash", "shell"
            ev.tool["input"] = {"command": shlex.join(map(str, cmd)) if isinstance(cmd, list) else str(cmd or "")}
        if ev.tool and ev.tool.get("native_name") == "apply_patch":
            ev.tool["name"], ev.tool["class"] = "Edit", "write"
            patch = str((raw.get("tool_input") or {}).get("input") or (raw.get("tool_input") or {}).get("patch") or "")
            ev.tool["input"] = {"file_path": _patch_path(patch), "old_string": "", "new_string": patch}
        return ev


def _patch_path(patch: str) -> str:
    for line in patch.splitlines():
        for marker in ("*** Update File: ", "*** Add File: ", "*** Delete File: ", "+++ b/"):
            if line.startswith(marker):
                return line[len(marker):].strip()
    return ""
