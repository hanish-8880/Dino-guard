"""Phase 9: evals (safe code evaluator, lifecycle, sentiment), assistant (ask your data), MCP server."""
import io
import json

import pytest

from dinoguard.assistant.mcp_server import handle, serve_stdio
from dinoguard.assistant.nl2sql import ask
from dinoguard.evals import manage
from dinoguard.evals.runner import run_for_session, session_dict
from dinoguard.evals.safe_eval import UnsafeExpression, safe_eval
from dinoguard.evals.sentiment import frustration, prompt_score
from dinoguard.hook.client import run


@pytest.mark.parametrize("expr", [
    "__import__('os').system('x')", "().__class__.__bases__", "open('x')", "(lambda: 1)()",
    "[x for x in ().__class__.__mro__]", "_secret", "exec('1')", "'a' * 10000000000",
])
def test_safe_eval_blocks(expr):
    with pytest.raises((UnsafeExpression, NameError)):
        safe_eval(expr, {"_secret": 1})


def test_safe_eval_allows():
    env = {"denies": 2, "tools": {"Bash": 3}, "prompts": ["fix it"], "errors": 0}
    assert safe_eval("denies == 0", env) is False
    assert safe_eval("{'score': 1 - min(1, denies / 4), 'unit': 'ratio'}", env) == {"score": 0.5, "unit": "ratio"}
    assert safe_eval("sum(v for v in [1, 2, 3]) > 5 and errors == 0", env) is True
    assert safe_eval("contains(prompts[0], 'FIX')", env) is True
    with pytest.raises(UnsafeExpression):
        safe_eval("tools.keys()", env)


def test_sentiment():
    assert prompt_score("please add a test for the parser") == 0
    assert prompt_score("WTF you deleted my file AGAIN!!") >= 0.8
    assert frustration(["thanks", "no, that's not it, still broken"]) > 0.3
    assert frustration([]) == 0


def _session(sid="E1", prompts=("add tests",), deny=False, end=True):
    base = {"session_id": sid, "cwd": "/w", "transcript_path": None}
    for p in prompts:
        run("claude", json.dumps({**base, "hook_event_name": "UserPromptSubmit", "prompt": p}))
    run("claude", json.dumps({**base, "hook_event_name": "PreToolUse", "tool_name": "Bash",
                              "tool_input": {"command": "rm -rf x" if deny else "ls"}}))
    if end:
        run("claude", json.dumps({**base, "hook_event_name": "SessionEnd", "reason": "other"}))


def test_session_end_runs_builtin_sentiment(conn):
    _session("happy", ["add a unit test please"])
    _session("angry", ["fix the bug", "WTF this is still broken, you broke it again!!"])
    res = {r["session_id"]: r for r in manage.results(conn, "sentiment")}
    assert res["happy"]["passed"] == 1 and res["angry"]["passed"] == 0
    assert res["angry"]["score"] < res["happy"]["score"]


def test_eval_lifecycle(conn):
    _session("a", deny=True)
    _session("b")
    d = session_dict(conn, "a")
    assert d["denies"] == 1 and d["tool_calls"] == 1 and d["prompts"] == ["add tests"]
    manage.new(conn, "no-denies", "code", "denies == 0", condition="tool_calls > 0")
    tested = {r["session_id"]: r["passed"] for r in manage.test(conn, "no-denies")}
    assert tested == {"a": False, "b": True}
    assert manage.results(conn, "no-denies") == []           # test never stores
    assert manage.deploy(conn, "no-denies") == 1
    assert manage.backfill(conn, "no-denies") == 2
    assert manage.backfill(conn, "no-denies") == 0           # idempotent
    _session("c")
    assert len(manage.results(conn, "no-denies")) == 3        # new sessions scored on SessionEnd
    manage.new(conn, "no-denies", "code", "denies < 2")
    assert manage.deploy(conn, "no-denies") == 2
    names = {e["name"]: e for e in manage.list_evals(conn)}
    assert names["no-denies"]["version"] == 2 and "sentiment" in names


def test_worker_eval(conn):
    _session("w1")
    manage.new(conn, "external", "worker", "")
    manage.deploy(conn, "external")
    pend = manage.worker_pending(conn, "external")
    assert [p["id"] for p in pend] == ["w1"]
    assert manage.worker_store(conn, "external", [{"session_id": "w1", "score": 0.9, "reasoning": "ok"}]) == 1
    assert manage.worker_pending(conn, "external") == []
    assert run_for_session(conn, "w1") == 0


def test_ask_templates(conn):
    _session("q1", deny=True)
    r = ask(conn, "which policies blocked the most this week?")
    assert r["method"] == "template" and r["columns"][0] == "policy" and r["rows"][0][0] == "no-rm-rf"
    assert "q1" in r["session_ids"]
    r2 = ask(conn, "which sessions burned the most tokens this week?")
    assert r2["sql"].lower().startswith("select") and not r2.get("error")


def test_mcp_protocol(conn):
    _session("m1", deny=True)
    init = handle(conn, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "dinoguard"
    assert handle(conn, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = handle(conn, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert {t["name"] for t in tools} == {"list_sessions", "get_trace", "query", "list_findings", "explain_decision"}
    out = handle(conn, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "list_sessions", "arguments": {}}})
    assert json.loads(out["result"]["content"][0]["text"])[0]["id"] == "m1"
    ev_id = conn.execute("SELECT id FROM events WHERE decision='deny'").fetchone()[0]
    ex = handle(conn, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "explain_decision", "arguments": {"event_id": ev_id}}})
    assert json.loads(ex["result"]["content"][0]["text"])["policies"][0]["policy"] == "no-rm-rf"
    bad = handle(conn, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                        "params": {"name": "query", "arguments": {"sql": "DELETE FROM events"}}})
    assert bad["result"]["isError"] is True
    assert handle(conn, {"jsonrpc": "2.0", "id": 6, "method": "nope"})["error"]["code"] == -32601


def test_mcp_stdio_loop():
    inp = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\nnot json\n")
    out = io.StringIO()
    serve_stdio(inp, out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert lines[0]["result"] == {} and lines[1]["error"]["code"] == -32700
