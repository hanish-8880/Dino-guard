# OpenAI Codex CLI adapter

Verified 2026-11-26 against https://learn.chatgpt.com/docs/hooks (developers.openai.com/codex/hooks redirects
there; the page is undated and is the reference for released behaviour) and, at tag `rust-v0.172.0`
(2026-11-24), `codex-rs/hooks/schema/generated/pre-tool-use.command.{input,output}.schema.json` and
`codex-rs/hooks/src/events/pre_tool_use.rs`.

Install: `dinoguard install --harness codex` writes `~/.codex/hooks.json` (`$CODEX_HOME`), or `.codex/hooks.json`
for a trusted project. **Then open Codex and trust the hooks once with `/hooks`.** Codex won't run unreviewed
hooks, and editing a hook resets its trust. No feature flag is needed (`[features] hooks = false` turns hooks off).

| Canonical event | Can DinoGuard block? | How |
|---|---|---|
| PreToolUse | yes | `hookSpecificOutput.permissionDecision: "deny"` (+ reason); instruct → `additionalContext` |
| PermissionRequest | yes | deny JSON |
| UserPromptSubmit | yes; instruct → `additionalContext` | JSON |
| Stop / SubagentStop | yes (workflow gates) | `{"decision":"block","reason"}` starts a continuation |
| PreCompact | yes | `continue: false` / exit 2 |
| PostToolUse | feedback replaces the tool result (can't undo) | instruct → `additionalContext` |
| SessionStart, SessionEnd, SubagentStart, Pre/PostModelRequest, Interrupt | observe | — |

Payload: `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `model`, `permission_mode`, `turn_id`,
`tool_name`, `tool_use_id`, `tool_input` (+ `agent_id`/`agent_type`). The shell tool (and unified exec) is reported
as `Bash` with a **string** `command`; `apply_patch` puts the patch text in `tool_input.command` (DinoGuard reads the
file from the patch header). MCP tools arrive as `mcp__server__tool`.

**Codex fails open** on hook crashes, other exit codes and unsupported output fields; exit 2 with an empty stderr
doesn't block either. DinoGuard therefore always answers its own failures (daemon down, bad payload) with exit 2
plus a non-empty reason and the deny JSON, and only sends `additionalContext` on the events that accept it.

Not covered by Codex hooks at all: hosted tools such as web search. Codex itself calls tool hooks "a useful
guardrail, not a complete enforcement boundary".
