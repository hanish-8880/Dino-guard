# OpenAI Codex CLI adapter (partly unverified)

Read 2026-11-25: developers.openai.com/codex/config-advanced and /config-reference (both redirect). They document
lifecycle hooks in `~/.codex/hooks.json` or `config.toml` (`[[hooks.PreToolUse]]` + `matcher` +
`[[hooks.PreToolUse.hooks]]` with `type = "command"`), enabled with `[features] hooks = true`. Events: PreToolUse,
PermissionRequest, PostToolUse, UserPromptSubmit, Stop, SubagentStart/Stop, SessionStart/End, PreCompact/PostCompact,
Pre/PostModelRequest, Interrupt.

**Not found on those pages:** the stdin payload and the block format. The adapter therefore assumes the
Claude-compatible shape (`hook_event_name`, `tool_name`, `tool_input`; deny via `permissionDecision` JSON or exit 2).
Check https://developers.openai.com/codex/hooks and adjust `dinoguard/adapters/codex.py` before relying on it.

Extra tool mapping: `shell` / `exec_command` (argv list) → Bash; `apply_patch` → Edit (file taken from the patch
header). Install writes `~/.codex/hooks.json`; you must enable `features.hooks` yourself.
