# Deep Agents adapter (LangChain)

Two surfaces, verified 2026-11-26.

## Deep Agents Code CLI (`dcode`) — `dinoguard install --harness deepagents`

Source: https://docs.langchain.com/oss/python/deepagents/code/hooks.md and …/cli/configuration.md.
Hooks file: `~/.deepagents/hooks.json` (user) or `.deepagents/hooks.json` (project; needs workspace trust, and
`--trust-project-hooks` in headless/CI runs). The payload and responses match Claude Code's, so this adapter reuses
the Claude Code one.

| Canonical event | Can DinoGuard block? | How |
|---|---|---|
| PreToolUse, PermissionRequest | yes | `permissionDecision: "deny"` |
| UserPromptSubmit | yes; instruct → `additionalContext` | JSON |
| Stop | yes (continue the turn, at most 8 times in a row) | `{"decision":"block"}` |
| PreCompact | yes | exit 2 |
| PostToolUse, SubagentStop | feedback only | — |
| SessionStart, SessionEnd, Notification, SubagentStart | observe | — |

**Limitation:** `dcode` fails **open**: if a hook errors or times out, the tool runs. There is no fail-closed
setting. The `{"hooks": {...}}` wrapper in the file is our reading of the docs (unverified); `updatedInput` is not
supported, which DinoGuard doesn't use.

## `deepagents` Python SDK — in-process middleware

```python
from deepagents import create_deep_agent
import dinoguard
from dinoguard.frameworks import deepagents_middleware

t = dinoguard.trace_session(agent_id="research")
agent = create_deep_agent(tools=[...], middleware=[deepagents_middleware(t)])
```

A denied call never runs; the model receives an error `ToolMessage` with DinoGuard's reason. Tool names
(`execute`, `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`, `task`) map to Bash/Read/Write/Edit/
Glob/Grep/Task. The returned-ToolMessage shape is our reading of the docs (unverified with the library installed);
the core logic is tested without it (`guard_tool_request`). Source: …/deepagents/customization and
…/langchain/middleware/custom.md; `deepagents` 0.7.42.
