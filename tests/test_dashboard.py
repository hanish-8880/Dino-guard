"""Phase 5: dashboard APIs (home, sessions, trace + graph, live stream, policies, metrics, queries)."""
import json

import pytest
from fastapi.testclient import TestClient

from dinoguard.dashboard.app import _token, create_app
from dinoguard.hook.client import run


def hook(p):
    return run("claude", json.dumps(p))


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture
def seeded(dg_home):
    base = {"session_id": "S1", "cwd": "/w/app", "transcript_path": None}
    hook({**base, "hook_event_name": "UserPromptSubmit", "prompt": "fix the build"})
    hook({**base, "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_use_id": "a"})
    hook({**base, "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_use_id": "a",
          "tool_response": {"stdout": "x"}, "duration_ms": 12})
    hook({**base, "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "rm -rf src"}, "tool_use_id": "b"})
    hook({**base, "hook_event_name": "PreToolUse", "tool_name": "Task", "tool_input": {"description": "explore"}, "tool_use_id": "c"})
    hook({**base, "hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": "/w/app/a.py"},
          "agent_id": "agent-123456789", "tool_use_id": "d"})
    return "S1"


def test_index_has_token(client):
    r = client.get("/")
    assert r.status_code == 200 and _token() in r.text and "__DG_TOKEN__" not in r.text
    assert client.get("/runner").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/../app.py").status_code == 404


def test_home_and_sessions(client, seeded):
    h = client.get("/api/home").json()
    assert h["actions"] == 4 and h["denied"] == 1 and h["allowed"] == 3
    s = client.get("/api/sessions").json()
    assert s[0]["id"] == "S1" and s[0]["first_prompt"] == "fix the build" and s[0]["denies"] == 1
    assert client.get("/api/sessions?harness=cursor").json() == []


def test_trace_nesting_graph_and_blocked(client, seeded):
    t = client.get("/api/sessions/S1/trace").json()
    pre_ls = next(i for i in t["items"] if i.get("tool") == "Bash" and i["input"].get("command") == "ls")
    assert pre_ls["children"] and pre_ls["children"][0]["event"] == "PostToolUse"
    assert t["blocked"][0]["policy"] == "no-rm-rf"
    assert t["blocked"][0]["message"].startswith("[DinoGuard:no-rm-rf]")
    ids = {n["id"] for n in t["graph"]["nodes"]}
    assert {"main", "agent-123456789"} <= ids
    assert t["graph"]["edges"][0]["via"] == "explore"
    assert client.get("/api/sessions/nope/trace").status_code == 404


def test_stream(client, seeded):
    r = client.get("/api/stream").json()
    assert r["records"] and r["offset"] > 0
    again = client.get(f"/api/stream?after={r['offset']}").json()
    assert again["records"] == []
    hook({"session_id": "S1", "cwd": "/w", "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "pwd"}})
    nxt = client.get(f"/api/stream?after={r['offset']}").json()
    assert [x["summary"] for x in nxt["records"]] == ["pwd"]


def test_policies_toggle_requires_token(client, seeded):
    p = client.get("/api/policies").json()
    rm = next(x for x in p["policies"] if x["name"] == "no-rm-rf")
    assert rm["enabled"] and rm["activity"].get("deny") == 1
    assert client.post("/api/policies/no-rm-rf", json={"enabled": False}).status_code == 403
    ok = client.post("/api/policies/no-rm-rf", json={"mode": "observe"}, headers={"X-DinoGuard-Token": _token()})
    assert ok.status_code == 200
    bad = client.post("/api/policies/guard-self-protect", json={"enabled": False}, headers={"X-DinoGuard-Token": _token()})
    assert bad.status_code == 400
    act = client.get("/api/policies/no-rm-rf/activity").json()
    assert act[0]["decision"] == "deny"


def test_metrics(client, seeded):
    m = client.get("/api/metrics").json()
    assert m["hook_latency_ms"]["n"] >= 6 and m["hook_latency_ms"]["p50"] is not None
    assert m["tool_latency_ms"]["Bash"]["p50"] == 12


def test_queries_read_only(client, seeded):
    r = client.post("/api/query", json={"sql": "SELECT COUNT(*) FROM events"}).json()
    assert r["rows"][0][0] >= 6
    for bad in ["DELETE FROM events", "SELECT 1; DROP TABLE events", "PRAGMA table_info(events)",
                "WITH x AS (SELECT 1) DELETE FROM events"]:
        assert client.post("/api/query", json={"sql": bad}).status_code == 400
    hdr = {"X-DinoGuard-Token": _token()}
    assert client.post("/api/queries", json={"name": "t", "sql": "SELECT 1"}, headers=hdr).status_code == 200
    assert client.get("/api/queries").json()[0]["name"] == "t"


def test_query_timeout(conn):
    from dinoguard.store.queries import QueryError, safe_select
    with pytest.raises(QueryError):
        safe_select(conn, "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT COUNT(*) FROM c",
                    timeout_s=0.3)
