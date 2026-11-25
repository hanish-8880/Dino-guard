# Cursor adapter

Verified 2026-11-25 against https://cursor.com/docs/agent/hooks. Part of the page was summarised, so per-event
payload fields beyond the common ones should be re-checked when Cursor updates.

Install: `dinoguard install --harness cursor [--scope user|project]` writes `~/.cursor/hooks.json` or
`<project>/.cursor/hooks.json` as `{"version": 1, "hooks": {"<event>": [{"command", "timeout": 180, "failClosed": true}]}}`.
`failClosed: true` makes Cursor block if the hook itself crashes.

| Cursor event | Canonical | Can block? | deny output | instruct output |
|---|---|---|---|---|
| preToolUse | PreToolUse (Shell→Bash, Read, Write, Grep, Delete→Other, Task, `MCP:x`) | yes | `{"permission":"deny","agent_message","user_message"}` | `agent_message` (best effort) |
| beforeShellExecution | PreToolUse Bash | yes | same | same |
| beforeMCPExecution | PreToolUse `MCP:server:tool` | yes | same | same |
| beforeReadFile | PreToolUse Read | yes | same | – |
| beforeSubmitPrompt | UserPromptSubmit | yes | `{"continue": false, "user_message"}` | – |
| stop / subagentStop | Stop / SubagentStop | yes ("not done yet") | `{"followup_message"}` | – |
| postToolUse(+Failure), afterFileEdit, afterShell/MCPExecution | PostToolUse | observe | `additional_context` | `additional_context` |
| sessionStart / sessionEnd / subagentStart / preCompact | same | observe | – | sessionStart: `additional_context` |

Session id = `conversation_id`; cwd = `cwd`, else the first `workspace_roots` entry. Fail-closed: exit 2 + deny JSON.
