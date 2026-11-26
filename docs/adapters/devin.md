# Devin CLI adapter

Verified 2026-11-26 against https://docs.devin.ai/cli/extensibility/hooks/overview.md,
https://docs.devin.ai/cli/extensibility/hooks/lifecycle-hooks.md and the stable changelog (latest v3000.15.14,
2026-11-25). The hook pages are undated.

Install: `dinoguard install --harness devin` (user: `~/.config/devin/config.json` under `"hooks"`, Windows
`%APPDATA%\devin\config.json`; project: `.devin/hooks.v1.json`).

**Devin also loads Claude Code's hook files by default** (`read_config_from.claude`). If DinoGuard is installed
for Claude Code, Devin already runs it: the hook sees `DEVIN_PROJECT_DIR` and switches to this adapter, so Devin's
tool names are understood. The installer refuses to add a second copy (that would check every action twice)
unless you turn that setting off and set `DG_DEVIN_FORCE=1`.

| Canonical event | Devin event | Can DinoGuard block? | How |
|---|---|---|---|
| PreToolUse | PreToolUse | yes | exit 2, reason on stderr |
| PermissionRequest | PermissionRequest | yes | exit 2 |
| UserPromptSubmit | UserPromptSubmit | yes | exit 2; instruct → `additionalContext` |
| Stop | Stop | yes (workflow gates) | `{"decision":"block","reason"}` |
| SessionStart | SessionStart | yes (refuses the session) | exit 2; instruct → `additionalContext` |
| PostToolUse | PostToolUse | observe (can't undo) | instruct → `additionalContext` |
| SessionEnd | SessionEnd | observe | — |

Tools: `exec`→Bash (`command`), `read`/`notebook_read`→Read, `write`→Write, `edit`/`notebook_edit`/`apply_patch`
→Edit, `grep`, `glob`, `webfetch`→WebFetch, `run_subagent`→Task, `mcp__<server>__<tool>`→MCP.

Unverified: the file-path field name for read/write/edit (we accept `file_path`, `path` and friends); whether
`additionalContext` works on PreToolUse (instruct there is delivered as a plain allow); the `fail_closed` hook
option's syntax. Exit codes other than 0/2 don't block in Devin, so a crash of `dinoguard-hook` fails open; the
daemon-down path still blocks because it answers exit 2.
