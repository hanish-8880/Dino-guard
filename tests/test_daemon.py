"""Phase 3: daemon round trip, fail-closed when down, version skew -> deny."""
import json

import pytest

from dinoguard import config
from dinoguard.daemon import client as dclient
from dinoguard.daemon.server import Daemon
from dinoguard.hook.client import run
from dinoguard.service import Service


def pre(cmd):
    return json.dumps({"session_id": "d1", "cwd": "/w", "hook_event_name": "PreToolUse", "tool_name": "Bash",
                       "tool_input": {"command": cmd}})


@pytest.fixture
def daemon():
    d = Daemon(Service()).bind()
    d.serve_in_thread()
    yield d
    d.shutdown()


def _enable():
    config.update(lambda c: c["daemon"].update(enabled=True))


def test_daemon_round_trip(daemon):
    _enable()
    assert dclient.health()["ok"]
    code, out, err = run("claude", pre("rm -rf src"))
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    code, out, err = run("claude", pre("ls"))
    assert code == 0 and out == ""


def test_daemon_down_denies_everything():
    _enable()
    code, out, err = run("claude", pre("ls"))
    assert code == 2 and "daemon" in err and "doctor" in err


def test_daemon_down_records_guard_down(dg_home):
    _enable()
    run("claude", pre("ls"))
    rec = json.loads((dg_home / "stream.jsonl").read_text().splitlines()[-1])
    assert rec["kind"] == "guard_down"


def test_version_skew_denies(daemon, monkeypatch):
    _enable()
    monkeypatch.setattr(dclient, "PROTOCOL_VERSION", 999)
    code, out, err = run("claude", pre("ls"))
    assert code == 2 and "mismatch" in err


def test_bad_token_rejected(daemon, monkeypatch):
    from dinoguard.daemon import transport
    if transport.HAS_UNIX:
        pytest.skip("token auth only used for TCP transport")
    _enable()
    real = transport.read_endpoint

    def fake():
        ep = real()
        ep["token"] = "wrong"
        return ep
    monkeypatch.setattr(transport, "read_endpoint", fake)
    code, out, err = run("claude", pre("ls"))
    assert code == 2


def test_stop_hook_not_trapped_when_down():
    _enable()
    code, out, err = run("claude", json.dumps({"session_id": "x", "hook_event_name": "Stop"}))
    assert code == 0
