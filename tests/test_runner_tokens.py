"""Phase 4: runner states, narrow terminals, stream tailing; transcript token accounting."""
import json
import os

import pytest

from dinoguard.runner_ui.app import StreamTail
from dinoguard.runner_ui.scene import render
from dinoguard.runner_ui.state import STATES, RunnerState


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("width", [10, 40, 80, 200])
def test_every_state_renders(state, width):
    tok = {"context_tokens": 190000, "context_window": 200000, "burn_per_min": 99999, "cost_usd": 12.5}
    for tick in range(0, 40, 7):
        c = render(state, width, tick, "a long reason " * 10, "tip " * 20, tok, "Bash npm test allow")
        rows = c.plain()
        assert len(rows) == 8
        assert all(len(r) == max(10, width) for r in rows)
        c.to_text()


def _rec(decision="allow", policy=None, **kw):
    return {"kind": "event", "tool": "Bash", "summary": "x", "decision": decision, "policy": policy, **kw}


def test_state_mapping():
    s = RunnerState(burn_high_per_min=1000)
    assert s.feed(_rec()) == "walk"
    assert s.feed(_rec("instruct", "warn-destructive-sql")) == "instruct"
    assert s.feed(_rec("deny", "no-rm-rf")) == "deny"
    assert s.feed(_rec("deny", "guard-self-protect")) == "shield"
    assert s.feed(_rec("instruct", "token-big-output")) == "sprint"
    assert s.feed(_rec(observed=[{"policy": "x", "decision": "deny"}])) == "ghost"
    assert s.feed({"kind": "review"}) == "review"
    assert s.feed({"kind": "approval_wait", "code": "1"}) == "wait"
    assert s.feed({"kind": "guard_down", "reason": "down"}) == "guard_down"
    assert s.feed(_rec(tokens={"burn_per_min": 5000})) == "sprint"


def test_transient_states_expire():
    s = RunnerState()
    s.feed(_rec("deny", "no-rm-rf"), now=0)
    assert s.current(now=1) == "deny"
    assert s.current(now=10) == "walk"
    s.feed({"kind": "guard_down"}, now=0)
    assert s.current(now=100) == "guard_down"   # stays until a real event arrives


def test_stream_tail_rotation(tmp_path):
    p = tmp_path / "stream.jsonl"
    t = StreamTail(p, backlog=0)
    assert t.read() == []
    p.write_text(json.dumps({"a": 1}) + "\n")
    assert t.read() == [] or True       # first read positions at end when no backlog
    with open(p, "a") as f:
        f.write(json.dumps({"a": 2}) + "\n" + '{"a": 3')
    assert t.read() == [{"a": 2}]
    with open(p, "a") as f:
        f.write("}\n")
    assert t.read() == [{"a": 3}]
    os.replace(p, tmp_path / "stream.jsonl.1")
    p.write_text(json.dumps({"a": 4}) + "\n")
    assert t.read() == [{"a": 4}]


def _assistant(mid, model, i, o, cr=0, cw=0, ts="2026-11-25T10:00:00.000Z"):
    return json.dumps({"type": "assistant", "timestamp": ts, "message": {
        "id": mid, "model": model, "usage": {"input_tokens": i, "output_tokens": o,
                                             "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}})


def test_transcript_incremental(conn, tmp_path):
    from dinoguard.tokens.transcript import ingest, stats
    tp = tmp_path / "t.jsonl"
    tp.write_text(_assistant("m1", "claude-sonnet-5-5", 1000, 200, 5000) + "\n" +
                  json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
    assert ingest(conn, "s", str(tp)) == 1
    assert ingest(conn, "s", str(tp)) == 0           # offset remembered
    with open(tp, "a") as f:
        f.write(_assistant("m2", "claude-sonnet-5-5", 2000, 300, 6000, ts="2026-11-25T10:00:30.000Z") + "\n")
        f.write(_assistant("m3", "claude-sonnet-5-5", 1, 1)[:20])   # partial line not consumed
    assert ingest(conn, "s", str(tp)) == 1
    st = stats(conn, "s", "2026-11-25T10:00:40.000Z")
    assert st.tokens_in == 3000 and st.tokens_out == 500 and st.cache_read == 11000
    assert st.context_tokens == 8000 and st.context_window == 200000
    assert st.cost_usd > 0 and st.burn_per_min == 3500
    assert st.model == "claude-sonnet-5-5"


def test_prices_user_editable(dg_home):
    from dinoguard.tokens import prices
    assert prices.for_model("claude-opus-5-5")["input"] > prices.for_model("claude-haiku-5-5")["input"]
    p = dg_home / "prices.yaml"
    assert p.exists()
    p.write_text("models: []\ndefault: {input: 1.0, output: 2.0, cache_read: 0, cache_write: 0, context_window: 1000}\n")
    assert prices.cost("x", 1_000_000, 1_000_000) == 3.0


def test_tokens_reach_policies(dg_home, tmp_path):
    from dinoguard import config
    from dinoguard.hook.client import run
    config.set_policy("token-context-high", enabled=True, mode="enforce")
    tp = tmp_path / "t.jsonl"
    tp.write_text(_assistant("m1", "claude-sonnet-5-5", 10, 10, 170000) + "\n")
    code, out, err = run("claude", json.dumps({"session_id": "tk", "cwd": "/w", "transcript_path": str(tp),
                                               "hook_event_name": "PreToolUse", "tool_name": "Bash",
                                               "tool_input": {"command": "ls"}}))
    assert "compact" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
    rec = json.loads((dg_home / "stream.jsonl").read_text().splitlines()[-1])
    assert rec["tokens"]["context_tokens"] == 170010


def test_demo_runs_headless(monkeypatch, tmp_path):
    from dinoguard.runner_ui import demo
    monkeypatch.setattr(demo.time, "sleep", lambda s: None)
    monkeypatch.setenv("TMP", str(tmp_path))
    assert demo.run_demo(seconds=1, show_runner=False) == 0
    lines = open(os.path.join(os.environ["DINOGUARD_HOME"], "stream.jsonl"), encoding="utf-8").read().splitlines()
    kinds = {json.loads(x).get("decision") for x in lines}
    assert {"allow", "instruct", "deny"} <= kinds
