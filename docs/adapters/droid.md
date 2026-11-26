# Factory Droid adapter

Verified 2026-11-26 against https://docs.factory.com/reference/hooks-reference.md,
https://docs.factory.com/cli/configuration/hooks-guide.md and the release notes (CLI v1.21.0, 2026-11-25).

Install: `dinoguard install --harness droid` (user `~/.factory/hooks.json`, project `.factory/hooks.json`). Every
hook is written with `"onFailure": "block"`, so if `dinoguard-hook` crashes or times out the tool is blocked.

| Canonical event | Droid event | Can DinoGuard block? | How |
|---|---|---|---|
| PreToolUse | PreToolUse | yes | `hookSpecificOutput.permissionDecision: "deny"` |
| PostToolUse | PostToolUse | feeds the reason back (can't undo) | `{"decision":"block"}` |
| UserPromptSubmit | UserPromptSubmit | yes; instruct → `additionalContext` | JSON |
| Stop / SubagentStop | Stop / SubagentStop | Stop: yes (workflow gates) | `{"decision":"block","reason"}` |
| PreCompact | PreCompact | manual compaction only | exit 2 |
| SessionStart | SessionStart | observe; instruct → `additionalContext` | JSON |
| SessionEnd, Notification | same | observe | — |

Droid does **not** send `additionalContext` to the model from PreToolUse or Stop, so an instruct there is
delivered as a plain allow (recorded, visible in the runner and dashboard).

Tools: `Execute`→Bash, `Create`→Write, `Edit`, `Read`, `ApplyPatch`→Edit (path parsed from the patch), `Grep`,
`Glob`, `LS`→Glob, `Task`, `FetchUrl`→WebFetch, `WebSearch`. If the hook is ever run through Claude Code settings,
`FACTORY_PROJECT_DIR` tells DinoGuard it is Droid.

Unverified: the Edit old/new text field names and the Read/Grep/Glob/LS/FetchUrl field names (DinoGuard accepts
the common spellings; `dinoguard doctor` reports drift).
