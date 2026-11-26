"""Phase 13 (CLAUDE.md §17.11-12): MCP write tools with approval, MCP over HTTP, plain-words audit answers,
git-checkpoint undo hint."""
import json
import shutil
import subprocess
import threading
import urllib.request

import pytest

from dinoguard import config
from dinoguard.approvals.flow import answer
from dinoguard.assistant.mcp_server import handle


def call(conn, name, args, transport="stdio"):
    r = handle(conn, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}},
               transport)["result"]
    return r["isError"], (json.loads(r["content"][0]["text"]) if not r["isError"] else r["content"][0]["text"])


def test_write_tool_needs_human_yes_for_the_exact_action(conn):
    conn.execute("INSERT INTO insights(id, source, status, title) VALUES ('i1','audit','open','x')")
    conn.commit()
    err, res = call(conn, "resolve_insight", {"insight_id": "i1", "comment": "fixed in #12"})
    assert not err and res["status"] == "pending_approval"
    code = res["approval_code"]
    assert conn.execute("SELECT status FROM insights WHERE id='i1'").fetchone()[0] == "open"   # nothing applied yet
    err, msg = call(conn, "resolve_insight", {"insight_id": "i1", "comment": "fixed in #12", "approval_code": code})
    assert err and "not answered" in msg
    answer(conn, code, True)                        # the human says yes (CLI / dashboard / phone)
    err, msg = call(conn, "resolve_insight", {"insight_id": "i1", "comment": "other text", "approval_code": code})
    assert err and "different action" in msg        # approval is bound to the exact arguments
    err, res = call(conn, "resolve_insight", {"insight_id": "i1", "comment": "fixed in #12", "approval_code": code})
    assert not err and res["status"] == "applied"
    row = conn.execute("SELECT status, resolved_at, comments_json FROM insights WHERE id='i1'").fetchone()
    assert row["status"] == "resolved" and row["resolved_at"] and "human-approved" in row["comments_json"]
    err, msg = call(conn, "resolve_insight", {"insight_id": "i1", "comment": "fixed in #12", "approval_code": code})
    assert err and "already used" in msg            # one approval = one change


def test_set_policy_mode_and_refusals(conn):
    err, res = call(conn, "set_policy_mode", {"name": "no-sudo", "mode": "observe"})
    code = res["approval_code"]
    answer(conn, code, False)
    err, msg = call(conn, "set_policy_mode", {"name": "no-sudo", "mode": "observe", "approval_code": code})
    assert err and "declined" in msg
    _, res = call(conn, "set_policy_mode", {"name": "no-sudo", "mode": "observe"})
    answer(conn, res["approval_code"], True)
    err, res = call(conn, "set_policy_mode", {"name": "no-sudo", "mode": "observe", "approval_code": res["approval_code"]})
    assert not err and config.load()["policies"]["no-sudo"]["mode"] == "observe"
    err, msg = call(conn, "set_policy_mode", {"name": "guard-self-protect", "mode": "disabled"})
    assert err and "always on" in msg
    err, msg = call(conn, "set_policy_mode", {"name": "nope", "mode": "observe"})
    assert err and "unknown policy" in msg
    err, msg = call(conn, "ack_insight", {"insight_id": "missing"})
    assert err


def test_approval_request_is_sent_to_the_channel(conn, monkeypatch):
    sent = []
    monkeypatch.setattr("dinoguard.assistant.mcp_write._send", lambda ch, text, cfg: sent.append((ch, text)))
    config.update(lambda c: c.__setitem__("approvals", {"channel": "phone", "timeout_s": 120}))
    _, res = call(conn, "run_audit", {"days": 7})
    assert sent and sent[0][0] == "phone" and res["approval_code"] in sent[0][1]


def test_mcp_calls_log_only_name_and_success(conn):
    call(conn, "query", {"sql": "SELECT 1"})
    call(conn, "query", {"sql": "DELETE FROM events"})
    logged = [tuple(r) for r in conn.execute("SELECT tool, ok, transport FROM mcp_calls ORDER BY ts")]
    assert logged == [("query", 1, "stdio"), ("query", 0, "stdio")]
    cols = [r[1] for r in conn.execute("PRAGMA table_info(mcp_calls)")]
    assert cols == ["id", "ts", "tool", "ok", "transport"]       # no arguments or results stored


def test_mcp_over_http_needs_token(conn):
    from dinoguard.assistant.mcp_http import make_server
    from dinoguard.store import db
    srv = make_server(0, token="secret-token", conn_factory=db.connect)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/mcp"

    def post(body, token=None):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, None
    try:
        assert post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[0] == 401
        assert post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, "wrong")[0] == 401
        code, body = post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, "secret-token")
        assert code == 200 and any(t["name"] == "run_audit" for t in body["result"]["tools"])
        code, body = post([{"jsonrpc": "2.0", "id": 2, "method": "ping"},
                           {"jsonrpc": "2.0", "method": "notifications/initialized"}], "secret-token")
        assert code == 200 and body == [{"jsonrpc": "2.0", "id": 2, "result": {}}]
        assert post({"jsonrpc": "2.0", "method": "notifications/initialized"}, "secret-token")[0] == 202
    finally:
        srv.shutdown()
        srv.server_close()
    assert srv.server_address[0] == "127.0.0.1"


def test_plain_words_audit_question(conn):
    from dinoguard.assistant.nl2sql import ask
    empty = ask(conn, "why does my agent keep failing?")
    assert empty["method"] == "audit" and "run `dinoguard audit`" in empty["notes"][0]
    conn.execute("INSERT INTO findings(id, audit_id, failure_mode, severity, count, session_ids, created_at) VALUES "
                 "('f1','a1','no-rm-rf: destructive deletes','high',4,'[\"s1\",\"s2\"]','2026-11-26T00:00:00Z')")
    conn.commit()
    res = ask(conn, "What goes wrong in my sessions?")
    assert res["method"] == "audit" and res["rows"][0][0].startswith("no-rm-rf")
    assert res["session_ids"] == ["s1", "s2"] and "evidence for review" in res["notes"][-1]
    assert ask(conn, "which sessions cost the most")["method"] != "audit"


# ---- undo hint ---------------------------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git not installed")
    r = tmp_path / "repo"
    r.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)  # noqa: E731
    g("init", "-q")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "t")
    (r / "app.py").write_text("print('v1')\n" * 300, encoding="utf-8")
    g("add", ".")
    g("commit", "-qm", "init")
    (r / "app.py").write_text("print('v2 uncommitted')\n" * 300, encoding="utf-8")
    return r


def test_risky_detection(tmp_path):
    from dinoguard.undo import risky_reason
    assert risky_reason("Bash", {"command": "rm -r build"}, None)
    assert risky_reason("Bash", {"command": "git checkout -- ."}, None)
    assert risky_reason("Bash", {"command": "git reset --hard HEAD~1"}, None)
    assert risky_reason("Bash", {"command": "sed -i 's/a/b/' x.py"}, None)
    assert risky_reason("Bash", {"command": "echo hi > src/app.py"}, None)
    assert not risky_reason("Bash", {"command": "ls -la"}, None)
    assert not risky_reason("Bash", {"command": "git status"}, None)
    assert not risky_reason("Bash", {"command": "echo hi > out.log"}, None)
    big = tmp_path / "big.txt"
    big.write_text("x" * 5000)
    assert risky_reason("Write", {"file_path": "big.txt"}, str(tmp_path))
    assert not risky_reason("Write", {"file_path": "new.txt"}, str(tmp_path))


def test_checkpoint_recorded_before_allowed_risky_action(conn, repo, capsys):
    from dinoguard.cli.main import main
    from dinoguard.hook.client import run
    base = {"session_id": "U1", "cwd": str(repo), "hook_event_name": "PreToolUse", "tool_name": "Bash"}
    run("claude", json.dumps({**base, "tool_input": {"command": "git checkout -- app.py"}, "tool_use_id": "u1"}))
    run("claude", json.dumps({**base, "tool_input": {"command": "rm -rf /"}, "tool_use_id": "u2"}))    # denied
    run("claude", json.dumps({**base, "tool_input": {"command": "ls"}, "tool_use_id": "u3"}))          # not risky
    cps = [dict(r) for r in conn.execute("SELECT * FROM checkpoints")]
    assert len(cps) == 1 and cps[0]["ref"] and cps[0]["reason"].startswith("risky shell")
    # the working tree is untouched and the stash list is empty
    assert "v2 uncommitted" in (repo / "app.py").read_text(encoding="utf-8")
    assert subprocess.run(["git", "-C", str(repo), "stash", "list"], capture_output=True, text=True).stdout == ""
    # the checkpoint really holds the uncommitted content
    show = subprocess.run(["git", "-C", str(repo), "show", f"{cps[0]['ref']}:app.py"], capture_output=True, text=True)
    assert "v2 uncommitted" in show.stdout
    assert main(["undo", cps[0]["event_id"][:14]]) == 0
    out = capsys.readouterr().out
    assert f"checkout {cps[0]['ref']} -- <path>" in out and "never restores" in out
    assert main(["undo"]) == 0
    trace = __import__("dinoguard.dashboard.trace", fromlist=["build_trace"]).build_trace(conn, "U1")
    assert any(i.get("checkpoint") for i in trace["items"])


def test_checkpoints_can_be_turned_off(conn, repo):
    from dinoguard.hook.client import run
    config.update(lambda c: c.__setitem__("undo", {"checkpoints": False}))
    run("claude", json.dumps({"session_id": "U2", "cwd": str(repo), "hook_event_name": "PreToolUse",
                              "tool_name": "Bash", "tool_input": {"command": "rm -r build"}}))
    assert conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0
