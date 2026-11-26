"""Phase 12 (CLAUDE.md §17.1-3, 17.7-8): retention, redaction config, environment, drift, analytics, export."""
import json

import pytest
from fastapi.testclient import TestClient

from dinoguard import config, paths
from dinoguard.analytics import compare, errors, export, stats
from dinoguard.hook.client import run
from dinoguard.secrets import scanner
from dinoguard.store import drift, retention


def hook(p):
    return run("claude", json.dumps(p))


BASE = {"cwd": "/w/app", "transcript_path": None}


def seed(sid="S1", cmd="ls", fail=False):
    b = {**BASE, "session_id": sid}
    hook({**b, "hook_event_name": "UserPromptSubmit", "prompt": "fix the build"})
    hook({**b, "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}, "tool_use_id": sid + "a"})
    hook({**b, "hook_event_name": "PostToolUseFailure" if fail else "PostToolUse", "tool_name": "Bash",
          "tool_input": {"command": cmd}, "tool_use_id": sid + "a", "duration_ms": 40,
          "tool_response": {"stdout": "x"} if not fail else "Error: cannot open /w/app/build/out-123.log (code 2)"})
    hook({**b, "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "rm -rf src"},
          "tool_use_id": sid + "b"})


# ---- 17.1 data safety --------------------------------------------------------------------------------------

def _age(conn, days):
    old = retention.cutoff_iso(days)
    conn.execute("UPDATE events SET ts=? WHERE session_id='OLD'", (old,))
    conn.execute("UPDATE sessions SET last_ts=?, started_at=?, status='ended', ended_at=? WHERE id='OLD'", (old, old, old))
    conn.commit()


def test_prune_deletes_only_old_data(conn):
    seed("OLD")
    seed("NEW")
    _age(conn, 120)
    with open(paths.stream_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "event", "ts": retention.cutoff_iso(200)}) + "\n")
    dry = retention.prune(conn, 90, dry_run=True)
    assert dry["dry_run"] and dry["events"] == 4 and dry["sessions"] == 1 and dry["stream_lines"] == 1
    assert conn.execute("SELECT COUNT(*) FROM events WHERE session_id='OLD'").fetchone()[0] == 4
    res = retention.prune(conn, 90)
    assert res["events"] == 4 and res["decisions"] >= 1
    assert conn.execute("SELECT COUNT(*) FROM events WHERE session_id='OLD'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE id='OLD'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events WHERE session_id='NEW'").fetchone()[0] == 4
    assert all(json.loads(x)["ts"] >= res["cutoff"] for x in paths.stream_path().read_text(encoding="utf-8").splitlines())


def test_prune_runs_once_a_day_and_can_be_off(conn):
    assert retention.run_due(conn, {"retention_days": 0}) is None
    assert retention.run_due(conn, {"retention_days": 30}) is not None
    assert retention.run_due(conn, {"retention_days": 30}) is None      # already pruned today
    with pytest.raises(ValueError):
        retention.prune(conn, 0)


def test_extra_redaction_patterns_and_no_output_mode(conn):
    config.update(lambda c: c.__setitem__("redaction", {"extra_patterns": [
        {"name": "employee_id", "pattern": r"EMP-\d{6}"}, {"name": "bad", "pattern": "("}], "store_outputs": False}))
    scanner.reset_cache()
    assert scanner.redact("user EMP-123456 logged in") == "user [REDACTED:employee_id] logged in"
    hook({**BASE, "session_id": "R", "hook_event_name": "PreToolUse", "tool_name": "Bash",
          "tool_input": {"command": "grep EMP-654321 users.csv"}, "tool_use_id": "r1"})
    hook({**BASE, "session_id": "R", "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "r1",
          "tool_input": {"command": "grep"}, "tool_response": {"stdout": "row with private data"}})
    rows = conn.execute("SELECT input_json, output_excerpt FROM events WHERE session_id='R'").fetchall()
    assert "EMP-654321" not in json.dumps([dict(r) for r in rows])
    assert all(r["output_excerpt"] is None for r in rows)
    scanner.reset_cache()


def test_environment_label(conn, monkeypatch):
    monkeypatch.setenv("DG_ENV", "staging")
    seed("E1")
    assert conn.execute("SELECT environment FROM sessions WHERE id='E1'").fetchone()[0] == "staging"
    monkeypatch.delenv("DG_ENV")
    assert config.environment({"environment": "ci"}) == "ci" and config.environment({}) == "local"


# ---- 17.7 hook drift ---------------------------------------------------------------------------------------

def test_hook_drift_records_unknown_events_and_tools(conn):
    hook({**BASE, "session_id": "D", "hook_event_name": "BrandNewEvent", "version": "9.9.9"})
    hook({**BASE, "session_id": "D", "hook_event_name": "PreToolUse", "tool_name": "MysteryTool", "tool_input": {}})
    hook({**BASE, "session_id": "D", "hook_event_name": "PreToolUse", "tool_name": "TodoWrite", "tool_input": {}})
    hook({**BASE, "session_id": "D", "hook_event_name": "BrandNewEvent"})
    got = {(d["kind"], d["value"]): d for d in drift.recent(conn)}
    assert got[("event", "BrandNewEvent")]["count"] == 2
    assert got[("event", "BrandNewEvent")]["harness_version"] == "9.9.9"
    assert ("tool", "MysteryTool") in got and ("tool", "TodoWrite") not in got
    # the unknown event is still stored, never dropped
    assert conn.execute("SELECT COUNT(*) FROM events WHERE event='BrandNewEvent'").fetchone()[0] == 2
    from dinoguard.cli.core import drift_checks
    assert any("BrandNewEvent" in d[2] for d in drift_checks())


# ---- 17.3 analytics ----------------------------------------------------------------------------------------

def test_error_fingerprints_group_similar_errors(conn):
    a = "Error: cannot open /w/app/build/out-123.log (code 2)"
    b = "Error: cannot open /tmp/x/y.log (code 7)"
    assert errors.fingerprint(a) == errors.fingerprint(b)
    assert errors.fingerprint(a) != errors.fingerprint("permission denied")
    assert errors.normalise(r"is C:\Users\me\proj\file.py") == "is <path>"
    seed("G1", fail=True)
    seed("G2", fail=True)
    g = errors.groups(conn, days=1)
    assert g[0]["count"] == 2 and g[0]["sessions"] == ["G1", "G2"] and g[0]["tools"] == {"Bash": 2}


def test_tool_and_policy_analytics(conn):
    seed("T1")
    seed("T2", fail=True)
    tools = {t["tool"]: t for t in stats.tools(conn, days=1)}
    assert tools["Bash"]["calls"] == 4 and tools["Bash"]["denied"] == 2 and tools["Bash"]["errors"] == 1
    assert tools["Bash"]["error_rate"] == 0.5 and tools["Bash"]["p50_ms"] == 40
    pols = {p["policy"]: p for p in stats.policies(conn, days=1)}
    assert pols["no-rm-rf"]["deny"] == 2


# ---- 17.2 compare + export ---------------------------------------------------------------------------------

def test_compare_two_sessions_and_groups(conn):
    seed("CA")
    seed("CB", fail=True)
    hook({**BASE, "session_id": "CC", "hook_event_name": "PreToolUse", "tool_name": "Read",
          "tool_input": {"file_path": "/w/app/a.py"}})
    r = compare.compare_two(conn, "CA", "CB")
    assert r["a"]["errors"] == 0 and r["b"]["errors"] == 1 and r["delta"]["errors"] == 1
    assert {t["tool"] for t in r["tools"]} == {"Bash"}
    groups = {g["group"]: g for g in compare.compare_groups(conn, "outcome", days=1)}
    assert groups["failed"]["sessions"] == 2 and groups["clean"]["sessions"] == 1
    assert compare.compare_groups(conn, "harness", days=1)[0]["group"] == "claude"
    with pytest.raises(ValueError):
        compare.compare_groups(conn, "colour")
    with pytest.raises(KeyError):
        compare.summary(conn, "nope")


def test_export_is_redacted_and_jsonl(conn):
    key = "AKIA" + "QWERTYUIOPASDFGH"
    hook({**BASE, "session_id": "X1", "hook_event_name": "PreToolUse", "tool_name": "Bash",
          "tool_input": {"command": f"echo {key}"}, "tool_use_id": "x"})
    doc = export.export_session(conn, "X1")
    assert doc["format"] == "dinoguard-trace" and doc["session"]["id"] == "X1"
    text = json.dumps(doc) + export.to_jsonl(doc)
    assert key not in text and "REDACTED" in text
    lines = export.to_jsonl(doc).splitlines()
    assert json.loads(lines[0])["type"] == "session" and len(lines) == 2
    assert export.export_session(conn, "X")["session"]["id"] == "X1"     # prefix works


def test_dashboard_endpoints(conn):
    from dinoguard.dashboard.app import create_app
    seed("W1")
    seed("W2", fail=True)
    c = TestClient(create_app())
    assert {s["id"] for s in c.get("/api/sessions?has_deny=true").json()} == {"W1", "W2"}
    assert [s["id"] for s in c.get("/api/sessions?session=W2").json()] == ["W2"]
    assert c.get("/api/sessions?event=PostToolUseFailure").json()[0]["id"] == "W2"
    assert c.get("/api/sessions?environment=local").json()
    assert c.get("/api/sessions?min_cost=5").json() == []
    assert c.get("/api/sessions/W1/export").json()["session"]["id"] == "W1"
    assert c.get("/api/sessions/W1/export?format=jsonl").text.startswith('{"type": "session"')
    assert c.get("/api/sessions/zzz/export").status_code == 404
    assert c.get("/api/compare?a=W1&b=W2").json()["delta"]["errors"] == 1
    assert c.get("/api/compare?by=outcome").json()["groups"]
    assert c.get("/api/compare?by=bad").status_code == 400
    assert c.get("/api/errors").json()[0]["count"] == 1
    assert c.get("/api/analytics/tools").json()[0]["tool"] == "Bash"
    assert c.get("/api/analytics/policies").json()
    assert "pages_analytics.js" in c.get("/").text
    assert c.get("/static/pages_analytics.js").status_code == 200


# ---- 17.8 alert routing ------------------------------------------------------------------------------------

def test_alert_severity_routes():
    from dinoguard.alerts.engine import route_channels
    cfg = {"alerts": {"routes": {"critical": ["wa"], "warning": ["slack"]}}}
    assert route_channels([], "critical", cfg) == ["wa"]
    assert route_channels(None, "warning", cfg) == ["slack"]
    assert route_channels(["mail"], "critical", cfg) == ["mail"]       # an alert's own channels win
    assert route_channels([], "critical", {}) == []


def test_cli_commands_run(conn, capsys):
    from dinoguard.cli.main import main
    seed("K1")
    seed("K2", fail=True)
    assert main(["errors", "--days", "1"]) == 0
    assert main(["compare", "K1", "K2"]) == 0
    assert main(["compare", "--by", "outcome", "--days", "1"]) == 0
    assert main(["export", "K1", "--format", "jsonl"]) == 0
    assert main(["prune", "--days", "30", "--dry-run"]) == 0
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "error groups" in out and "would delete" in out and "capabilities" in out
