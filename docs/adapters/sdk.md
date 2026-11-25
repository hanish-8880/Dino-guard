# SDK (custom agents)

```python
from dinoguard import check
d = check(tool="Bash", input={"command": "rm -rf build"}, session_id="run-42")
if d.kind == "deny":
    ...   # d.reason is written for the agent
```

Helpers in `dinoguard.integrations`:
- `@guard(tool=...)`: decorator that raises `DinoGuardDenied` on deny and records the output.
- `guard_tools([...])`: wraps LangChain/LangGraph `Tool.func`, CrewAI `BaseTool._run` and LlamaIndex
  `FunctionTool.fn`.
- `langgraph_tool_node_guard(tool_call)`: for ToolNode-style dicts.

The SDK uses the daemon when configured (fail closed), and evaluates in-process otherwise. From a shell or another
language, pipe `{"event","tool","input","session_id","cwd"}` to `dinoguard-hook sdk`.

OpenTelemetry: POST OTLP/JSON to `http://127.0.0.1:8030/api/v1/traces` while the dashboard runs. Spans become
observe-only events, and `gen_ai.*` usage attributes become model calls.
