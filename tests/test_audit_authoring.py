"""Phase 8: audit replay, findings/insights, drafting, backtest, publish/deploy/rollback, observe->enforce."""
import json

import pytest

from dinoguard import config
from dinoguard.audit.replay import overview, parse_transcript, run_audit
from dinoguard.audit.schedule import list_audits, set_schedule, unschedule
from dinoguard.authoring.backtest import backtest
from dinoguard.authoring.draft import draft_from_finding, draft_policy
from dinoguard.authoring.draft import test_policy as run_examples
from dinoguard.deploy import versions
from dinoguard.engine.registry import build
from dinoguard.hook.client import run


def _line(**kw):
    return json.dumps(kw)


def write_transcript(dg_home_parent, name="sess-a"):
    d = dg_home_parent / "claudehome" / "projects" / "-w-app"
    d.mkdir(parents=True, exist_ok=True)
    base = {"sessionId": name, "cwd": "/w/app", "timestamp": "2026-11-25T09:00:00Z"}
    lines = [
        _line(type="user", message={"role": "user", "content": "clean up the build"}, **base),
        _line(type="assistant", message={"content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                                                       "input": {"command": "rm -rf build"}}]}, **base),
        _line(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}, **base),
        _line(type="assistant", message={"content": [{"type": "tool_use", "id": "t2", "name": "Read",
                                                       "input": {"file_path": "/w/app/.env"}}]}, **base),
        _line(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "t2",
                                                 "content": "API_KEY=AKIA" + "QWERTYUIOPASDFGH"}]}, **base),
    ] + [_line(type="assistant", message={"content": [{"type": "tool_use", "id": f"n{i}", "name": "Bash",
                                                        "input": {"command": "npm test"}}]}, **base) for i in range(4)]
    (d / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_parse_transcript(tmp_path):
    write_transcript(tmp_path)
    evs = parse_transcript(next((tmp_path / "claudehome" / "projects").rglob("*.jsonl")))
    kinds = [e.event for e in evs]
    assert kinds[:3] == ["UserPromptSubmit", "PreToolUse", "PostToolUse"]
    assert evs[1].tool_input["command"] == "rm -rf build"


def test_audit_findings_and_insights(conn, tmp_path):
    write_transcript(tmp_path)
    res = run_audit(conn, days=3650)
    assert res["sessions"] == 1 and res["tool_calls"] == 6
    modes = {f["policy"]: f for f in res["findings"]}
    assert {"no-rm-rf", "no-env-read", "secret-in-output", "loop-detected"} <= set(modes)
    assert modes["no-rm-rf"]["severity"] == "high"
    assert "AKIA" not in json.dumps(res)            # evidence is redacted
    ins = conn.execute("SELECT * FROM insights WHERE source='audit'").fetchall()
    assert ins and all(i["status"] == "open" for i in ins)
    ov = overview(conn)
    assert ov["sessions"] == 1 and ov["policy_hits"] > 0 and ov["by_policy"]
    # draft from a finding
    fid = conn.execute("SELECT id FROM findings WHERE failure_mode LIKE 'no-rm-rf%'").fetchone()["id"]
    d = draft_from_finding(conn, fid)
    assert d["name"].startswith("custom-") and run_examples(d["name"])["passed"]


def test_audit_schedule(conn):
    set_schedule(conn, 7, "me@example.com")
    a = list_audits(conn)
    assert a[0]["cadence_days"] == 7
    with pytest.raises(ValueError):
        set_schedule(conn, 91)
    unschedule(conn)
    assert list_audits(conn)[0]["cadence_days"] is None


def test_draft_publish_deploy_backtest_rollback(conn, dg_home):
    hook = lambda cmd: run("claude", json.dumps({"session_id": "bt", "cwd": "/w", "hook_event_name": "PreToolUse",
                                                 "tool_name": "Bash", "tool_input": {"command": cmd}}))
    for cmd in ("terraform destroy -auto-approve", "ls", "terraform plan"):
        hook(cmd)
    d = draft_policy("never run terraform destroy")
    name = d["name"]
    assert run_examples(name)["passed"]
    bt = backtest(name, days=1, conn=conn)
    assert bt["blocked"] == 1 and bt["hits"][0]["summary"].startswith("terraform destroy")

    v1 = versions.publish(name, "first", conn=conn)
    assert v1 == 1 and versions.publish(name, conn=conn) == 1          # identical code => same version
    versions.deploy(name, v1, "observe", conn=conn)
    reg = build("/w", packs=False)
    p = reg.get(name)
    assert p is not None and p.source == "deployed" and p.version == 1
    code, out, _ = hook("terraform destroy")
    # built-in terraform policy is off by default; deployed one is in observe -> allowed, but recorded
    assert out == ""
    rec = json.loads((dg_home / "stream.jsonl").read_text().splitlines()[-1])
    assert any(o["policy"] == name for o in rec["observed"])

    versions.deploy(name, v1, "enforce", conn=conn)
    code, out, _ = hook("terraform destroy")
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"

    code2 = d["code"].replace("is not allowed here", "is blocked (v2)")
    v2 = versions.publish(name, "v2", code=code2, conn=conn)
    versions.deploy(name, v2, "enforce", conn=conn)
    assert versions.rollback(name, conn=conn) == 1
    assert [r["version"] for r in versions.list_versions(name, conn=conn)] == [1, 2]
    assert config.load()["policies"][name]["mode"] == "enforce"


def test_publish_rejects_builtin_name(conn):
    code = "from dinoguard import policy, allow\n@policy('no-rm-rf')\ndef f(ctx):\n    return allow()\n"
    with pytest.raises(ValueError):
        versions.publish("no-rm-rf", code=code, conn=conn)
