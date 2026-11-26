# SDK (custom agents)

## One check

```python
from dinoguard import check
d = check(tool="Bash", input={"command": "rm -rf build"}, session_id="run-42")
if d.kind == "deny":
    ...   # d.reason is written for the agent
```

## Lifecycle tracer (CLAUDE.md §17.9)

```python
import dinoguard

with dinoguard.trace_session(agent_id="support-bot", environment="staging") as t:
    t.prompt("refund order 42")
    t.model_request("claude-sonnet-5-5", messages=3)
    t.model_response("claude-sonnet-5-5", tokens_in=1200, tokens_out=300, latency_ms=900)
    t.run_tool("Bash", {"command": "ls"}, lambda: subprocess.check_output(["ls"]))  # check -> run -> record
    t.human_wait("needs approval"); t.human_input("yes")
    t.hook_start("lint"); t.hook_end("lint")
    t.pause(); t.resume(); t.interruption("user pressed stop")
```

- `tool_call` / `prompt` raise `DinoGuardDenied` on deny (pass `raise_on_deny=False` to get the decision back).
- `stop()` returns the turn-end decision; a deny means a workflow gate says "not done yet".
- Leaving the `with` block sends `SessionEnd` (and `AgentError` first if an exception escaped), so evals run.
- `model_response` becomes a model-call row: cost is priced from `prices.yaml` unless you pass `cost_usd`.

| Tracer call | Canonical event | Can block? |
|---|---|---|
| `prompt` | UserPromptSubmit | yes |
| `tool_call` | PreToolUse | yes |
| `stop` | Stop | yes (workflow gates) |
| `tool_result` | PostToolUse / PostToolUseFailure | observe (can't undo) |
| `start` / `end` | SessionStart / SessionEnd | observe |
| `model_request` / `model_response` | ModelRequest / ModelResponse | observe |
| `hook_start` / `hook_end` | HookStart / HookEnd | observe |
| `human_wait` / `human_input` | HumanWait / HumanInput | observe |
| `interruption`, `error`, `pause`, `resume` | Interruption, AgentError, AgentPause, AgentResume | observe |

## Framework helpers (`dinoguard.frameworks`, no framework imported unless used)

- `langchain_handler(tracer)`: LangChain / LangGraph callback (model calls with token usage, tool calls that can be
  denied, tool results and errors). Pass it in `config={"callbacks": [...]}`.
- `traced_tools(tools, tracer)`: wraps LangChain `Tool.func`, CrewAI `BaseTool._run`, LlamaIndex `FunctionTool.fn`
  (sync and async).
- `pydantic_ai_tool(tracer)`: decorator under `@agent.tool`; the signature is kept so the schema is unchanged.
- `crewai_step_callback(tracer)`: CrewAI `step_callback=` (observe only; use `traced_tools` to block).
- Older helpers in `dinoguard.integrations`: `@guard`, `guard_tools`, `langgraph_tool_node_guard`.

These helpers rely only on public attribute names (`func`, `_run`, `fn`, callback method names). They are tested
with stand-in objects, not with the frameworks installed.

## Any language: HTTP

`POST http://127.0.0.1:8030/api/v1/events` while `dinoguard dashboard` runs. Header `X-DinoGuard-Token` = the
contents of `dashboard.token` in the DinoGuard home. Body: one event, a list, or `{"events": [...]}`:

```json
{"event": "PreToolUse", "tool": "Bash", "input": {"command": "ls"}, "session_id": "run-42",
 "agent_id": "bot", "environment": "staging", "cwd": "/srv/app", "harness": "my-agent"}
```

Response: `{"decisions": [{"decision": "allow", "reason": null, "policy": null}]}`. The whole HTTP API is
described at `http://127.0.0.1:8030/openapi.json`.

TypeScript / JavaScript: `sdk/ts/dinoguard.mjs` (+ `dinoguard.d.ts`), no dependencies, no build step. It fails
closed: if DinoGuard is unreachable, tool calls are denied (`failClosed: false` to opt out).

From a shell: pipe the same JSON to `dinoguard-hook sdk`.

## OpenTelemetry

POST OTLP/JSON to `http://127.0.0.1:8030/api/v1/traces` while the dashboard runs. Spans become observe-only events,
and `gen_ai.*` usage attributes become model calls.
