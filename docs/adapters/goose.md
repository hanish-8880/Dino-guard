# Goose adapter

Verified 2026-11-26 against https://goose-docs.ai/docs/guides/context-engineering/hooks/ (undated),
https://goose-docs.ai/blog/2026/05/14/goose-hooks (2026-05-14) and
https://goose-docs.ai/docs/guides/managing-tools/tool-permissions. The docs don't say which Goose version added
hooks. (Until 2026-11-25 this adapter was a stub, because no hook mechanism was documented.)

Install: `dinoguard install --harness goose` writes a plugin folder in the Open Plugins layout:
`~/.agents/plugins/dinoguard/` (user) or `<project>/.agents/plugins/dinoguard/` (project), containing
`plugin.json` and `hooks/hooks.json`. Each command passes its event name: `dinoguard-hook goose PreToolUse`.
Matchers are `".*"` (a bare `"*"` is invalid in Goose and the rule is silently skipped).

| Canonical event | Goose event | Can DinoGuard block? | How |
|---|---|---|---|
| PreToolUse | PreToolUse | yes | stdout `{"decision":"block","reason"}` |
| Stop | Stop | yes (workflow gates; capped by `GOOSE_STOP_HOOK_BLOCK_CAP`) | same JSON |
| PostToolUse / PostToolUseFailure | same | observe (can't undo) | — |
| UserPromptSubmit, SessionStart, SessionEnd | same | observe | — |

Fail closed: the PreToolUse group has `on_failure: "block"`, so a crash, timeout or missing decision blocks the
tool. Goose's default is `allow` (fail open), and Stop hooks always fail open. Where `on_failure` sits in the file
(the matcher group, as written here) is our reading of the docs; if Goose ignores it there, the hook fails open.
Check with `dinoguard doctor` and Goose's own hook logs after installing.

Tools are `<extension>__<tool>`: `developer__shell`→Bash (`command`); `developer__text_editor`→Read / Write / Edit
by its `command` (`view`, `write`, `str_replace`, `insert`; fields `path`, `file_text`, `old_str`, `new_str`). The
text_editor field names come from Goose's developer extension and are not on the hooks page (unverified). Tools of
other extensions are treated as MCP tools (`MCP:<extension>:<tool>`).

Goose's built-in tool permissions (Always Allow / Ask Before / Never Allow) stay as they are; DinoGuard runs in
addition to them.
