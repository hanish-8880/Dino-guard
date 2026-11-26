"""OpenAI Codex CLI hooks adapter. Verified 2026-11-26 against learn.chatgpt.com/docs/hooks (was
developers.openai.com/codex/hooks) and the PreToolUse JSON schemas at tag rust-v0.172.0. See docs/adapters/codex.md.

Claude-shaped stdin (session_id, transcript_path, cwd, hook_event_name, tool_name, tool_use_id, tool_input, ...).
The shell tool arrives as `Bash` with a string `command`; `apply_patch` carries the patch text in `command`.
Deny = `permissionDecision: "deny"` JSON (or exit 2 with a non-empty stderr). Codex fails OPEN on any other hook
failure, so DinoGuard's own failures always answer with that explicit deny.
"""
from __future__ import annotations

import shlex

from .claude_code import ClaudeCodeAdapter


class CodexAdapter(ClaudeCodeAdapter):
    name = "codex"
    VERIFIED_VERSION = "rust-v0.172.0 (2026-11-24); hooks reference read 2026-11-26"
    known_native_tools = None   # Codex tool names differ from Claude Code's
    blocking_events = frozenset({"PreToolUse", "PermissionRequest", "UserPromptSubmit", "Stop", "SubagentStop",
                                 "PreCompact"})
    CONTEXT_EVENTS = ("PreToolUse", "PostToolUse", "SessionStart", "UserPromptSubmit", "SubagentStart")

    def to_native(self, event, decision):
        # additionalContext is only accepted on these events; anything unsupported marks the hook Failed
        if decision.kind == "instruct" and event.event not in self.CONTEXT_EVENTS:
            from .base import NativeResponse
            return NativeResponse(0, "", "")
        return super().to_native(event, decision)

    def to_canonical(self, raw):
        ev = super().to_canonical(raw)
        ev.harness = "codex"
        if ev.tool and ev.tool.get("native_name") in ("shell", "exec_command", "local_shell"):
            cmd = (raw.get("tool_input") or {}).get("command")
            ev.tool["name"], ev.tool["class"] = "Bash", "shell"
            ev.tool["input"] = {"command": shlex.join(map(str, cmd)) if isinstance(cmd, list) else str(cmd or "")}
        if ev.tool and ev.tool.get("native_name") == "apply_patch":
            ev.tool["name"], ev.tool["class"] = "Edit", "write"
            tin = raw.get("tool_input") or {}
            patch = str(tin.get("command") or tin.get("input") or tin.get("patch") or "")
            ev.tool["input"] = {"file_path": _patch_path(patch), "old_string": "", "new_string": patch}
        return ev


def _patch_path(patch: str) -> str:
    for line in patch.splitlines():
        for marker in ("*** Update File: ", "*** Add File: ", "*** Delete File: ", "+++ b/"):
            if line.startswith(marker):
                return line[len(marker):].strip()
    return ""
