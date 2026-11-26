# Google Antigravity CLI (`agy`) adapter

Verified 2026-11-26 against https://antigravity.google/docs/hooks.md (shared by CLI, IDE and App; undated) and
the changelog (App v2.35.2, 2026-11-25).

Install: `dinoguard install --harness antigravity` (user `~/.gemini/config/hooks.json`, project
`.agents/hooks.json`). DinoGuard adds one named entry, `"dinoguard"`, next to your own hook entries, and the
uninstaller removes only that entry. The payload has no event-name field, so each command passes it:
`dinoguard-hook antigravity PreToolUse`. Files are written without a BOM (older App builds ignored BOM files).

| Canonical event | Antigravity event | Can DinoGuard block? | How |
|---|---|---|---|
| PreToolUse | PreToolUse | yes | `{"decision":"deny","reason"}`; allow = `{"decision":"allow"}` (defer to normal policy) |
| PostToolUse | PostToolUse | observe (can't undo) | `{}` |
| Stop | Stop | yes (workflow gates) | `{"decision":"continue","reason"}` keeps the agent going |
| ModelRequest / ModelResponse | PreInvocation / PostInvocation | instruct → injected system message | `injectSteps` |
| SessionStart | SessionStart | instruct → injected system message | `injectSteps` |

Exit codes and failure behaviour aren't documented, so DinoGuard always prints JSON and exits 0; when it can't
evaluate (bad payload, daemon down) it prints a deny for PreToolUse.

Tools (fields are PascalCase): `run_command`→Bash (`CommandLine`, `Cwd`), `view_file`→Read (`AbsolutePath`),
`write_to_file`→Write (`TargetFile`, `CodeContent`), `replace_file_content` / `multi_replace_file_content`→Edit,
`grep_search`→Grep, `find_by_name` / `list_dir`→Glob, `read_url_content`→WebFetch, `search_web`, `invoke_subagent`→Task.

Unverified: the exact deny wording (inferred from the decision table), MCP tool name format, and whether hooks run
outside the `agy` CLI (a forum post says CLI only).
