# Hermes Agent adapter (Nous Research)

Verified 2026-11-26 against https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks,
…/features/plugins, …/reference/tools-reference, and `tools/terminal_tool.py` / `tools/file_tools.py` on `main`.

Install: `dinoguard install --harness hermes` prints a `hooks:` block for your profile's `config.yaml`. DinoGuard
doesn't rewrite that file because it's yours (comments and all). Keep `fail_closed: true` on `pre_tool_call`;
without it Hermes lets the tool run when the hook crashes or times out. Approve the hook once in Hermes
(`hermes hooks list`), or use `--accept-hooks` / `hooks_auto_accept` for gateway, cron and CI runs.

| Canonical event | Hermes event | Can DinoGuard block? | How |
|---|---|---|---|
| PreToolUse | pre_tool_call | yes | stdout `{"decision":"block","reason"}` |
| PostToolUse | post_tool_call | observe (can't undo) | — |
| ModelRequest | pre_llm_call | instruct → context | stdout `{"context": "..."}` (goes into the user message) |
| ModelResponse | post_llm_call | observe | — |
| SessionStart / SessionEnd | on_session_start / on_session_end | observe | — |
| SubagentStart / SubagentStop | subagent_start / subagent_stop | observe | — |

Tools: `terminal`→Bash (`command`, `workdir` used as cwd), `read_file`→Read (`path`), `write_file`→Write,
`patch`→Edit (`old_string`/`new_string`, or `mode: patch`), `search_files`→Grep, `web_extract`→WebFetch,
`web_search`, `delegate_task`→Task.

Not used: Hermes' `{"action":"approve"}` (that asks a human in Hermes, it is not an allow) and input rewriting.
Unverified: where `post_tool_call` puts the tool result inside `extra` (we look for `result` / `tool_result`).
