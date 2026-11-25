# Claude Code adapter

Verified on 2026-11-25 against https://code.claude.com/docs/en/hooks (current hook reference).

## Wiring

`dinoguard install --harness claude --scope user|project|local` merges one command hook per event into
`~/.claude/settings.json`, `.claude/settings.json` or `.claude/settings.local.json`:

```json
{"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "<abs path>/dinoguard-hook claude"}]}]}}
```

`matcher` is only written for tool events (PreToolUse, PostToolUse, PostToolUseFailure, PermissionRequest);
UserPromptSubmit / Stop do not support matchers. The original file is backed up to `~/.dinoguard/backups/`, and
its exact bytes are kept in `~/.dinoguard/install_state.json` so `uninstall` restores it byte-for-byte (if the
file changed after install, only DinoGuard's handlers are removed).

## Input (stdin JSON)

Common: `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, (`agent_id`, `agent_type` in
subagents). Tool events add `tool_name`, `tool_input`, `tool_use_id`; PostToolUse adds `tool_response`
(+ optional `duration_ms`); PostToolUseFailure carries the error. UserPromptSubmit has `prompt`; Stop/SubagentStop
have `stop_hook_active`. On Windows, file paths arrive with backslashes.

## Tool mapping

| Claude Code tool | Canonical | Notes |
|---|---|---|
| Bash, **PowerShell** | Bash | `command`; PowerShell adds `shell: powershell` |
| Read / Write / Edit / Grep / Glob / WebFetch / WebSearch | same | |
| MultiEdit | Edit | old/new strings joined, original `edits` kept |
| NotebookEdit | Edit | `notebook_path` → `file_path`, `new_source` → `new_string` |
| Task / Agent | Task | |
| `mcp__server__tool` | `MCP:server:tool` | |
| anything else | Other | `_tool` holds the native name |

## Output mapping

| Event | deny | instruct | Can block? |
|---|---|---|---|
| PreToolUse | `hookSpecificOutput.permissionDecision: "deny"` + `permissionDecisionReason` (exit 0) | `hookSpecificOutput.additionalContext` | yes |
| PermissionRequest | `hookSpecificOutput.decision.behavior: "deny"` + `message` | – | yes |
| UserPromptSubmit | `{"decision":"block","reason"}` | `additionalContext` | yes |
| Stop / SubagentStop | `{"decision":"block","reason"}` → agent keeps working | `additionalContext` | yes |
| PreCompact | exit 2 + stderr | – | yes |
| PostToolUse / PostToolUseFailure | `{"decision":"block","reason"}` (reason shown to Claude; **side effects already happened**) | `additionalContext` | observe |
| SessionStart / SessionEnd / Notification / SubagentStart | ignored | SessionStart: `additionalContext` | observe |

`allow` prints nothing: DinoGuard never emits `permissionDecision: "allow"`, because that would skip the user's own
permission prompts.

## Fail-closed

If the daemon is configured but unreachable, times out, or speaks another protocol version, PreToolUse gets
exit 2 + a deny JSON whose reason tells the operator to run `dinoguard doctor`. Stop/SubagentStop are *not*
blocked in that state (that would trap the agent in an endless turn); they get a `systemMessage` instead.
Claude Code caps consecutive Stop-hook continuations (8 by default), which bounds any workflow-gate loop.
