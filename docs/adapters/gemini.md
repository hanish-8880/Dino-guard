# Gemini CLI adapter

Verified 2026-11-25 against https://geminicli.com/docs/hooks/ and https://geminicli.com/docs/hooks/reference/
(both read in full). The site says Gemini CLI was replaced by Antigravity CLI (June 2026) for some tiers, so
confirm your CLI supports hooks.

Install: `dinoguard install --harness gemini` merges into `~/.gemini/settings.json` (or `.gemini/settings.json`):
`{"hooks": {"BeforeTool": [{"matcher": ".*", "hooks": [{"name": "dinoguard", "type": "command", "command": "...", "timeout": 180000}]}]}}`.

| Gemini event | Canonical | Can block? | deny | instruct |
|---|---|---|---|---|
| BeforeTool | PreToolUse | yes | `{"decision":"deny","reason"}` | `systemMessage` (user-visible only) |
| AfterTool | PostToolUse / PostToolUseFailure (`tool_response.error`) | observe | `hookSpecificOutput.additionalContext` | same |
| BeforeAgent | UserPromptSubmit | yes (prompt discarded) | `{"decision":"deny"}` | `additionalContext` |
| AfterAgent | Stop | observe (deny semantics not documented) | – | – |
| SessionStart / SessionEnd / PreCompress / Notification | SessionStart / SessionEnd / PreCompact / Notification | observe | – | SessionStart: `additionalContext` |

Tool names: run_shell_command→Bash, read_file / read_many_files→Read, write_file→Write, replace→Edit,
glob / list_directory→Glob, search_file_content→Grep, web_fetch→WebFetch, google_web_search→WebSearch,
`mcp_<server>_<tool>`→`MCP:server:tool`. Fail-closed: exit 2 + deny JSON on BeforeTool.
