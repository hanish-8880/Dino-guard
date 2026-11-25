"""Phase 6: phone approvals + alerts engine + insights + channels."""
import hashlib
import hmac
import json
import threading
import time

import pytest

from dinoguard import config
from dinoguard.alerts import channels, engine, nl
from dinoguard.approvals import flow
from dinoguard.engine.decisions import ask_human
from helpers import bash


@pytest.mark.parametrize("text,expect", [
    ("tell me when denies > 5 in 10 minutes", {"trigger_kind": "metric", "metric": "denies", "op": ">", "value": 5, "window_s": 600}),
    ("errors more than 3 within 15 min", {"trigger_kind": "errors", "metric": "errors", "op": ">", "value": 3, "window_s": 900}),
    ("cost above $5 in 1 day", {"trigger_kind": "metric", "metric": "cost_usd", "op": ">", "value": 5, "window_s": 86400}),
    ("sentiment score < 0.4", {"trigger_kind": "eval_score", "eval": "sentiment", "op": "<", "value": 0.4}),
    ("sql: SELECT COUNT(*) FROM events WHERE decision='deny' > 10", {"trigger_kind": "sql", "op": ">", "value": 10}),
    ("tokens >= 2m in 1 hour", {"metric": "tokens", "op": ">=", "value": 2_000_000, "window_s": 3600}),
])
def test_nl_parse(text, expect):
    spec = nl.parse(text)
    for k, v in expect.items():
        assert spec[k] == v, (k, spec)


def test_nl_parse_rejects_nonsense():
    with pytest.raises(ValueError):
        nl.parse("make it nice")


def _deny_events(conn, n):
    from dinoguard.util import new_id, now_iso
    for _ in range(n):
        conn.execute("INSERT INTO events(id, session_id, ts, event, decision) VALUES (?,?,?,?,?)",
                     (new_id("e"), "s", now_iso(), "PreToolUse", "deny"))
    conn.commit()


def test_alert_fires_once_and_opens_insight(conn):
    a = engine.create_from_text(conn, "denies > 2 in 10 minutes")
    assert engine.check_all(conn, {}, force=True) == []
    _deny_events(conn, 3)
    fired = engine.check_all(conn, {}, force=True)
    assert len(fired) == 1 and fired[0]["value"] == 3
    assert engine.check_all(conn, {}, force=True) == []          # still in breach: no re-fire
    ins = conn.execute("SELECT * FROM insights").fetchall()
    assert len(ins) == 1 and ins[0]["alert_id"] == a["id"] and ins[0]["status"] == "open"
    assert engine.preview(conn, a["id"])["would_fire"] == 1


def test_consecutive_checks(conn):
    a = engine.create(conn, "x", nl.parse("denies > 0 in 1 hour"), consecutive_checks=2)
    _deny_events(conn, 1)
    assert engine.check_all(conn, {}, force=True) == []
    assert len(engine.check_all(conn, {}, force=True)) == 1
    assert a


def test_sql_alert_read_only(conn):
    spec = nl.parse("sql: SELECT COUNT(*) FROM events > 0")
    _deny_events(conn, 1)
    assert engine.evaluate_spec(conn, spec)[0] is True
    bad = {"trigger_kind": "sql", "sql": "DELETE FROM events", "op": ">", "value": 0}
    assert engine.evaluate_spec(conn, bad) == (False, None)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_eval_score_alert(conn):
    from dinoguard.util import new_id, now_iso
    conn.execute("INSERT INTO evals(id, name, kind, version, enabled) VALUES ('ev1','sentiment','code',1,1)")
    conn.execute("INSERT INTO eval_results(id, eval_id, version, session_id, score, created_at) VALUES (?,?,?,?,?,?)",
                 (new_id("r"), "ev1", 1, "s", 0.2, now_iso()))
    conn.commit()
    assert engine.evaluate_spec(conn, nl.parse("sentiment score < 0.4"))[0]
    combo = {"trigger_kind": "eval_combo", "all": [{"eval": "sentiment", "op": "<", "value": 0.4, "window_s": 3600},
                                                  {"eval": "missing", "op": ">", "value": 0, "window_s": 3600}]}
    assert engine.evaluate_spec(conn, combo)[0] is False


def test_channel_routing_and_secret_storage(monkeypatch, dg_home):
    from dinoguard.approvals.setup import save_channel
    save_channel("ops", "slack", {"webhook_url": "https://hooks.example/x"})
    cfg = config.load()
    assert "webhook_url" not in json.dumps(cfg)
    sent = {}
    monkeypatch.setattr(channels, "SENDERS", {**channels.SENDERS, "slack": lambda ch, text: sent.update(ch=ch, text=text)})
    channels.send("ops", "hi")
    assert sent["text"] == "hi"
    with pytest.raises(channels.ChannelError):
        channels.send("nope", "x")


def test_webhook_signature():
    sig = channels.sign(b'{"a":1}', "s3cret")
    assert sig == "sha256=" + hmac.new(b"s3cret", b'{"a":1}', hashlib.sha256).hexdigest()


def test_reply_parsing():
    assert flow.parse_reply("YES 1234") == (True, "1234")
    assert flow.parse_reply("no") == (False, None)
    assert flow.parse_reply("maybe") is None


def _cfg():
    return {"approvals": {"channel": "phone", "timeout_s": 5}, "channels": {"phone": {"type": "telegram"}}}


def test_phone_approval_yes(conn, monkeypatch):
    monkeypatch.setattr(flow, "POLL_S", 0.05)
    msgs = []
    approver = flow.make_approver(_cfg(), conn, sender=lambda ch, text: msgs.append(text))
    code = {}

    def answer_later():
        for _ in range(100):
            p = flow.pending(conn)
            if p:
                code["c"] = p[0]["code"]
                flow.answer(conn, p[0]["code"], True)
                return
            time.sleep(0.02)
    from dinoguard.store import db
    other = db.connect()
    t = threading.Thread(target=lambda: (answer_later.__globals__.update(conn=other), answer_later()))
    t.start()
    d = approver(bash("terraform apply"), ask_human("needs approval", 5))
    t.join()
    assert d.kind == "allow" and msgs and code["c"] in msgs[0]


def test_phone_approval_timeout_and_no(conn, monkeypatch):
    monkeypatch.setattr(flow, "POLL_S", 0.05)
    approver = flow.make_approver(_cfg(), conn, sender=lambda ch, text: None)
    assert approver(bash("terraform apply"), ask_human("x", 0)).kind == "deny"
    approver2 = flow.make_approver({"approvals": {}}, conn)
    d = approver2(bash("terraform apply"), ask_human("x", 1))
    assert d.kind == "deny" and "not set up" in d.reason


def test_phone_approval_end_to_end_policy(dg_home, monkeypatch):
    """phone-approval policy -> engine hold -> approver -> deny when nobody answers."""
    import json as _j

    from dinoguard.hook.client import run
    monkeypatch.setattr(flow, "POLL_S", 0.05)
    config.set_policy("phone-approval", enabled=True, mode="enforce", params={"timeout_s": 0})
    config.update(lambda c: c.update(approvals={"channel": "phone", "timeout_s": 0},
                                     channels={"phone": {"type": "webhook", "url": "http://127.0.0.1:9/x"}}))
    code, out, err = run("claude", _j.dumps({"session_id": "p", "cwd": "/w", "hook_event_name": "PreToolUse",
                                             "tool_name": "Bash", "tool_input": {"command": "terraform apply"}}))
    reason = _j.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "phone-approval" in reason or "approval" in reason
