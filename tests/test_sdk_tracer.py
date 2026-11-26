"""Phase 13 (CLAUDE.md §17.9): SDK lifecycle tracer, HTTP event ingest, framework helpers, TS client."""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dinoguard
from dinoguard.tracer import DinoGuardDenied


def _events(conn, sid):
    return [(r["event"], r["decision"]) for r in conn.execute(
        "SELECT event, decision FROM events WHERE session_id=? ORDER BY ts, rowid", (sid,))]


def test_tracer_records_lifecycle_and_blocks(conn):
    with dinoguard.trace_session(agent_id="bot", environment="staging", session_id="T1") as t:
        t.prompt("tidy the repo")
        t.model_request("claude-sonnet-5-5", messages=2)
        t.model_response("claude-sonnet-5-5", tokens_in=1000, tokens_out=200, latency_ms=50)
        assert t.run_tool("Bash", {"command": "ls"}, lambda: "a.txt") == "a.txt"
        with pytest.raises(DinoGuardDenied) as e:
            t.tool_call("Bash", {"command": "rm -rf src"})
        assert e.value.decision.policy == "no-rm-rf"
        t.human_wait("approve?")
        t.human_input("yes")
        t.hook_start("lint")
        t.hook_end("lint")
        t.pause("waiting")
        t.resume()
        t.interruption("user pressed stop")
    evs = [e for e, _ in _events(conn, "T1")]
    assert evs == ["SessionStart", "UserPromptSubmit", "ModelRequest", "ModelResponse", "PreToolUse", "PostToolUse",
                   "PreToolUse", "HumanWait", "HumanInput", "HookStart", "HookEnd", "AgentPause", "AgentResume",
                   "Interruption", "SessionEnd"]
    assert ("PreToolUse", "deny") in _events(conn, "T1")
    s = conn.execute("SELECT * FROM sessions WHERE id='T1'").fetchone()
    assert s["environment"] == "staging" and s["model"] == "claude-sonnet-5-5" and s["cost_usd"] > 0
    assert s["status"] == "ended"
    mc = conn.execute("SELECT * FROM model_calls WHERE session_id='T1'").fetchone()
    assert mc["tokens_in"] == 1000 and mc["latency_ms"] == 50
    post = conn.execute("SELECT * FROM events WHERE session_id='T1' AND event='PostToolUse'").fetchone()
    pre = conn.execute("SELECT id FROM events WHERE session_id='T1' AND event='PreToolUse' LIMIT 1").fetchone()
    assert post["parent_id"] == pre["id"] and post["agent_id"] == "bot"     # linked by tool_use_id
    hook = json.loads(conn.execute("SELECT input_json FROM events WHERE session_id='T1' AND event='HookEnd'")
                      .fetchone()[0])
    assert hook["_extra"]["hook"] == "lint" and hook["_extra"]["duration_ms"] is not None


def test_tracer_error_and_failed_tool(conn):
    with pytest.raises(RuntimeError):
        with dinoguard.trace_session(session_id="T2", raise_on_deny=False) as t:
            assert t.tool_call("Bash", {"command": "sudo ls"}).kind == "deny"     # returned, not raised
            with pytest.raises(ValueError):
                t.run_tool("Read", {"file_path": "x.py"}, lambda: (_ for _ in ()).throw(ValueError("nope")))
            raise RuntimeError("boom")
    evs = _events(conn, "T2")
    assert ("PostToolUseFailure", "allow") in evs and ("AgentError", "allow") in evs and evs[-1][0] == "SessionEnd"
    assert conn.execute("SELECT error FROM events WHERE session_id='T2' AND event='AgentError'").fetchone()[0] \
        == "RuntimeError: boom"


def test_http_event_ingest(conn):
    from dinoguard.dashboard.app import _token, create_app
    c = TestClient(create_app())
    body = {"events": [{"event": "PreToolUse", "tool": "Bash", "input": {"command": "ls"}, "session_id": "H1"},
                       {"event": "PreToolUse", "tool": "Bash", "input": {"command": "git push --force"},
                        "session_id": "H1", "environment": "ci"}]}
    assert c.post("/api/v1/events", json=body).status_code == 403          # token required
    r = c.post("/api/v1/events", json=body, headers={"X-DinoGuard-Token": _token()}).json()
    assert [d["decision"] for d in r["decisions"]] == ["allow", "deny"]
    one = c.post("/api/v1/events", json={"event": "ModelResponse", "session_id": "H1",
                                         "extra": {"model": "x", "tokens_in": 5}},
                 headers={"X-DinoGuard-Token": _token()}).json()
    assert one["decisions"][0]["decision"] == "allow"
    assert c.post("/api/v1/events", json=[1], headers={"X-DinoGuard-Token": _token()}).status_code == 400
    assert "/api/v1/events" in c.get("/openapi.json").json()["paths"]


# ---- framework helpers (stand-in objects; no framework installed) ------------------------------------------

class Gen:
    def __init__(self, usage):
        self.message = type("M", (), {"usage_metadata": usage, "response_metadata": {"model_name": "m-1"}})()


class LLMResult:
    llm_output = None
    generations = [[Gen({"input_tokens": 70, "output_tokens": 30})]]


def test_langchain_handler(conn):
    from dinoguard.frameworks import langchain_handler
    t = dinoguard.trace_session(session_id="L1")
    h = langchain_handler(t)
    h.on_chat_model_start({"kwargs": {"model": "m-1"}}, [[1, 2]], run_id="r1")
    h.on_llm_end(LLMResult(), run_id="r1")
    h.on_tool_start({"name": "Bash"}, "ls", run_id="t1", inputs={"command": "ls"})
    h.on_tool_end("a.txt", run_id="t1")
    with pytest.raises(DinoGuardDenied):
        h.on_tool_start({"name": "Bash"}, "rm -rf /", run_id="t2", inputs={"command": "rm -rf /"})
    h.on_text("ignored")                         # unknown callbacks are no-ops
    assert h.raise_error is True
    evs = [e for e, _ in _events(conn, "L1")]
    assert evs == ["ModelRequest", "ModelResponse", "PreToolUse", "PostToolUse", "PreToolUse"]
    assert conn.execute("SELECT tokens_in, tokens_out FROM model_calls WHERE session_id='L1'").fetchone()[:] == (70, 30)


def test_traced_tools_and_pydantic_decorator(conn):
    from dinoguard.frameworks import pydantic_ai_tool, traced_tools
    t = dinoguard.trace_session(session_id="F1")

    class CrewTool:                              # CrewAI-style
        name = "Bash"

        def _run(self, command):
            return "ran " + command

    class LlamaTool:                             # LlamaIndex-style: fn attribute
        def __init__(self):
            self.name = "Read"
            self.fn = lambda file_path: "contents"

    crew, llama = traced_tools([CrewTool(), LlamaTool()], t)
    assert crew._run("ls") == "ran ls" and llama.fn(file_path="a.py") == "contents"
    with pytest.raises(DinoGuardDenied):
        crew._run("sudo rm x")

    @pydantic_ai_tool(t, name="WebFetch")
    async def fetch(ctx, url: str) -> str:
        return "page"
    assert asyncio.run(fetch(None, "https://example.com")) == "page"
    import inspect
    assert list(inspect.signature(fetch).parameters) == ["ctx", "url"]
    evs = _events(conn, "F1")
    assert evs.count(("PreToolUse", "allow")) == 3 and ("PreToolUse", "deny") in evs
    inp = json.loads(conn.execute("SELECT input_json FROM events WHERE session_id='F1' AND tool_name='WebFetch' "
                                  "AND event='PreToolUse'").fetchone()[0])
    assert inp == {"url": "https://example.com"}       # ctx is not recorded


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_ts_client_fails_closed_and_parses():
    js = Path(__file__).resolve().parents[1] / "sdk" / "ts" / "dinoguard.mjs"
    script = ("import { DinoGuard, DinoGuardDenied } from " + json.dumps(js.as_uri()) + ";"
              "const dg = new DinoGuard({ url: 'http://127.0.0.1:9', token: 't' });"
              "try { await dg.toolCall('Bash', {command: 'ls'}); console.log('allowed'); }"
              "catch (e) { console.log(e instanceof DinoGuardDenied ? 'denied:' + e.decision.policy : 'other'); }"
              "const open = new DinoGuard({ url: 'http://127.0.0.1:9', token: 't', failClosed: false });"
              "console.log((await open.toolCall('Bash', {command: 'ls'})).decision);")
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.stdout.split() == ["denied:dinoguard/unavailable", "allow"], out.stderr
