"""Phase 1 + 2 'Done when' checks, end to end through the Claude Code hook."""
import json

import pytest

from dinoguard.hook.client import run
from dinoguard.store import db


def hook(payload: dict):
    code, out, err = run("claude", json.dumps(payload))
    return code, (json.loads(out) if out.strip() else None), err


def pre(cmd=None, tool="Bash", inp=None, cwd="/w", sid="sess-1"):
    return {"session_id": sid, "transcript_path": None, "cwd": cwd, "permission_mode": "default",
            "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inp or {"command": cmd},
            "tool_use_id": "tu1"}


def decision_of(resp):
    code, out, err = resp
    if out and "hookSpecificOutput" in out:
        h = out["hookSpecificOutput"]
        if h.get("permissionDecision") == "deny":
            return "deny"
        if h.get("additionalContext"):
            return "instruct"
    if out and out.get("decision") == "block":
        return "deny"
    if code == 2:
        return "deny"
    return "allow"


@pytest.mark.parametrize("cmd,expected", [
    ("rm -rf src", "deny"),
    ("cat .env", "deny"),
    ("git push --force", "deny"),
    ('psql -c "DROP TABLE users"', "instruct"),
    ("ls", "allow"),
    ("dinoguard uninstall", "deny"),
    ("python -m dinoguard policies disable no-rm-rf", "deny"),
])
def test_done_when(cmd, expected):
    assert decision_of(hook(pre(cmd))) == expected


def test_disable_guard_by_editing_settings_denied(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "dinoguard-hook claude"}]}]}}')
    r = hook(pre(tool="Write", inp={"file_path": str(settings), "content": "{}"}))
    assert decision_of(r) == "deny"


def test_powershell_tool_is_shell(tmp_path):
    r = hook(pre(tool="PowerShell", inp={"command": "Remove-Item -Recurse -Force src"}))
    assert decision_of(r) == "deny"


def test_one_row_per_action_and_redaction():
    hook(pre("ls"))
    hook({"session_id": "sess-1", "cwd": "/w", "hook_event_name": "PostToolUse", "tool_name": "Bash",
          "tool_input": {"command": "cat creds"}, "tool_use_id": "tu1",
          "tool_response": {"stdout": "key=AKIA" + "QWERTYUIOPASDFGH", "stderr": ""}})
    hook({"session_id": "sess-1", "cwd": "/w", "hook_event_name": "UserPromptSubmit", "prompt": "hello"})
    c = db.connect()
    rows = c.execute("SELECT event, output_excerpt, parent_id FROM events ORDER BY ts").fetchall()
    assert [r["event"] for r in rows] == ["PreToolUse", "PostToolUse", "UserPromptSubmit"]
    assert "AKIA" not in (rows[1]["output_excerpt"] or "") and "REDACTED" in rows[1]["output_excerpt"]
    assert rows[1]["parent_id"] is not None
    dump = "\n".join(str(tuple(r)) for r in c.execute("SELECT * FROM events"))
    dump += "\n".join(str(tuple(r)) for r in c.execute("SELECT * FROM decisions"))
    assert "QWERTYUIOPASDFGH" not in dump


def test_stream_file_written(dg_home):
    hook(pre("ls"))
    lines = (dg_home / "stream.jsonl").read_text().splitlines()
    rec = json.loads(lines[-1])
    assert rec["decision"] == "allow" and rec["tool"] == "Bash" and rec["summary"] == "ls"


def test_stop_gate_block_format(dg_home):
    from dinoguard import config
    config.set_policy("stop-require-tests-pass", enabled=True, mode="enforce")
    sid = "s-stop"
    hook({"session_id": sid, "cwd": "/w", "hook_event_name": "PostToolUse", "tool_name": "Edit",
          "tool_input": {"file_path": "a.py", "old_string": "a", "new_string": "b"}, "tool_response": {}})
    code, out, err = hook({"session_id": sid, "cwd": "/w", "hook_event_name": "Stop", "stop_hook_active": False})
    assert out["decision"] == "block" and "tests" in out["reason"]


def test_garbage_payload_fails_closed():
    code, out, err = run("claude", "not json")
    assert code == 2 and "doctor" in err
