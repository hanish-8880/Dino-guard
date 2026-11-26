# Pi coding agent adapter

Verified 2026-11-26 against `@earendil-works/pi-coding-agent` 1.6.1 (published 2026-11-25): `docs/extensions.md`,
`docs/cli.md`, `docs/sessions.md` and the tool schemas in `dist/core/tools/*.js`; same as
https://docs.pi.dev/latest/extensions.

Pi has no shell-command hooks, only in-process extensions. `dinoguard install --harness pi` writes a small
extension (`~/.pi/agent/extensions/dinoguard.js`, or `.pi/extensions/dinoguard.js` for a project, which Pi loads
after you trust the project). It forwards each call to `dinoguard-hook pi` and maps the answer back.

| Canonical event | Pi event | Can DinoGuard block? | How |
|---|---|---|---|
| PreToolUse | tool_call | yes | returns `{block: true, reason}`; instruct → `pi.sendMessage(..., {deliverAs: "steer"})` |
| PostToolUse / PostToolUseFailure | tool_result | observe (can't undo) | — |
| SessionStart / SessionEnd | session_start / session_shutdown | observe | — |

Fails closed: if `dinoguard-hook` can't run or answers garbage, the extension blocks the tool (and Pi itself blocks
a tool when a `tool_call` handler throws).

Tools: `bash` / `powershell`→Bash, `read`→Read (`path`), `write`→Write, `edit`→Edit (`edits: [{oldText, newText}]`
joined), `grep`, `find`→Glob, `ls`→Glob, `mcp__<server>__<tool>`→MCP.

Unverified: how Pi shows the block `reason` to the model (presumably as the tool error), and the exact
`sendMessage` options for steering (wrapped in try/catch so a mismatch can't break the tool call).
