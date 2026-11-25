import time

from dinoguard import allow, deny, instruct, policy
from dinoguard.engine.authority import normalize
from dinoguard.engine.decisions import Decision, merge
from dinoguard.engine.registry import Registry, collect, load_source
from dinoguard.engine.runner import evaluate
from helpers import FakeProbe, bash, event


def _reg(*fns, builtins=False):
    r = Registry()
    if builtins:
        r.load_builtins()
    for fn in fns:
        p = fn.__dinoguard_policy__
        r.add(p)
    return r


def _enforce(*names):
    return {"policies": {n: {"mode": "enforce"} for n in names}}


@policy("p-deny", events=["PreToolUse"])
def p_deny(ctx):
    return deny("nope")


@policy("p-instr-a")
def p_instr_a(ctx):
    return instruct("tip a")


@policy("p-instr-b")
def p_instr_b(ctx):
    return instruct("tip b")


@policy("p-after")
def p_after(ctx):
    raise AssertionError("must not run after a deny")


@policy("p-slow")
def p_slow(ctx):
    time.sleep(2)
    return deny("too late")


@policy("p-boom")
def p_boom(ctx):
    raise RuntimeError("bug")


def test_first_deny_wins():
    r = _reg(p_instr_a, p_deny, p_after)
    ev = evaluate(bash("ls"), r, _enforce("p-instr-a", "p-deny", "p-after"), probe=FakeProbe())
    assert ev.decision.kind == "deny" and ev.decision.policy == "p-deny"
    assert "p-after" not in [x.policy for x in ev.results]


def test_instructs_are_merged():
    r = _reg(p_instr_a, p_instr_b)
    ev = evaluate(bash("ls"), r, _enforce("p-instr-a", "p-instr-b"), probe=FakeProbe())
    assert ev.decision.kind == "instruct"
    assert "tip a" in ev.decision.reason and "tip b" in ev.decision.reason


def test_merge_helper():
    assert merge([]).kind == "allow"
    assert merge([Decision("instruct", "x"), Decision("deny", "y")]).kind == "deny"


def test_timeout_is_allow():
    r = _reg(p_slow)
    ev = evaluate(bash("ls"), r, _enforce("p-slow"), probe=FakeProbe(), deadline_s=0.2)
    assert ev.decision.kind == "allow"
    assert "deadline" in (ev.results[0].error or "")


def test_exception_is_allow_and_logged():
    r = _reg(p_boom)
    ev = evaluate(bash("ls"), r, _enforce("p-boom"), probe=FakeProbe())
    assert ev.decision.kind == "allow" and "RuntimeError" in ev.results[0].error


def test_custom_policies_default_to_observe():
    r = _reg(p_deny)
    ev = evaluate(bash("ls"), r, {}, probe=FakeProbe())
    assert ev.decision.kind == "allow"
    assert ev.observed and ev.observed[0].policy == "p-deny"


def test_builtins_run_before_custom(builtins):
    r = Registry().load_builtins()
    r.add(p_instr_a.__dinoguard_policy__)
    names = [p.name for p in r.policies]
    assert names[0] == "guard-self-protect"
    assert names.index("p-instr-a") > names.index("no-rm-rf")


def test_self_protect_cannot_be_disabled():
    r = Registry().load_builtins()
    cfg = {"policies": {"guard-self-protect": {"enabled": False, "mode": "observe"}}}
    ev = evaluate(bash("dinoguard uninstall"), r, cfg, probe=FakeProbe())
    assert ev.decision.kind == "deny" and ev.decision.policy == "guard-self-protect"


def test_self_protect_error_is_deny(monkeypatch):
    r = Registry().load_builtins()
    p = r.get("guard-self-protect")

    def broken(ctx):
        raise RuntimeError("x")
    monkeypatch.setattr(p, "fn", broken)
    ev = evaluate(bash("ls"), r, {}, probe=FakeProbe())
    assert ev.decision.kind == "deny"


def test_malformed_authority_is_hard():
    assert normalize("reviewable", []) == ("hard", [])
    assert normalize("reviewable", None) == ("hard", [])
    assert normalize("weird", ["x"]) == ("hard", [])
    assert normalize(None) == ("hard", [])
    assert normalize("reviewable", ["on-task"]) == ("reviewable", ["on-task"])

    @policy("p-mal", authority="reviewable")
    def p_mal(ctx):
        return allow()
    assert p_mal.__dinoguard_policy__.authority == "hard"


def test_duplicate_names_rejected():
    r = Registry().load_builtins()
    src = "from dinoguard import policy, deny\n@policy('no-rm-rf')\ndef x(ctx):\n    return deny('dup')\n"
    for p in load_source(src, "dup.py"):
        assert r.add(p) is False
    assert any("duplicate" in e["error"] for e in r.errors)


def test_event_filter():
    r = _reg(p_deny)
    ev = evaluate(event(None, None, ev="Stop"), r, _enforce("p-deny"), probe=FakeProbe())
    assert ev.decision.kind == "allow"


def test_custom_file_loading(project, monkeypatch):
    d = project / ".dinoguard" / "policies"
    d.mkdir(parents=True)
    (d / "team_policies.py").write_text(
        "from dinoguard import policy, deny, allow\n"
        "@policy('no-payments', events=['PreToolUse'])\n"
        "def f(ctx):\n"
        "    return deny('no') if 'payments' in str(ctx.tool_input.get('file_path','')) else allow()\n")
    (d / "broken_policies.py").write_text("raise SystemExit('bad')\n")
    from dinoguard.engine.registry import build
    r = build(str(project), packs=False, deployed=False)
    assert r.get("no-payments") is not None
    assert any("broken_policies" in e["file"] for e in r.errors)
    ev = evaluate(event("Write", {"file_path": "src/payments/a.py", "content": "x"}, cwd=str(project)), r,
                  _enforce("no-payments"), probe=FakeProbe())
    assert ev.decision.kind == "deny"


def test_collect_finds_only_policies():
    ns = {"a": p_deny, "b": 1, "c": lambda: 0}
    assert [p.name for p in collect(ns)] == ["p-deny"]


def test_builtins_default_to_pretooluse_only():
    """Regression: a deny rule must not re-fire on the PostToolUse of the same command."""
    r = Registry().load_builtins()
    post = event("Bash", {"command": "rm -rf src"}, ev="PostToolUse", output="ok")
    ev = evaluate(post, r, {}, probe=FakeProbe())
    assert ev.decision.kind == "allow"
    assert not [x for x in ev.results if x.policy == "no-rm-rf"]
