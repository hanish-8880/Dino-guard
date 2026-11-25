"""Phase 7: LLM reviewer semantics with a fake model."""
import pytest

from dinoguard.engine.context import History
from dinoguard.engine.registry import Registry
from dinoguard.engine.runner import evaluate
from dinoguard.reviewer import checks as checks_mod
from dinoguard.reviewer.checks import CHECKS, check, precondition_holds
from dinoguard.reviewer.review import Reviewer, verdict_from
from helpers import FakeProbe, bash, event

CFG_ON = {"policies": {"no-kubectl-mutate": {"enabled": True, "mode": "enforce"},
                       "no-terraform-apply": {"enabled": True, "mode": "enforce"}}}


def fake(answer, calls=None):
    def llm(system, user, timeout):
        if calls is not None:
            calls.append(user)
        if isinstance(answer, Exception):
            raise answer
        return answer
    return llm


ON_TASK = {"probes": {"p1": False, "p2": False}, "exempt": True, "evidence": "weak", "rationale": "user asked"}
OFF_TASK = {"probes": {"p1": True, "p2": True}, "exempt": False, "evidence": "strong", "rationale": "unrelated"}


def _rev(answer, mode="enforce", calls=None, conn=None):
    return Reviewer({"reviewer": {"mode": mode, "timeout_s": 1}}, conn=conn, llm=fake(answer, calls))


def _hist(prompt="deploy the app to staging with kubectl"):
    return History([], first_prompt=prompt, prompts=[prompt])


@pytest.fixture
def reg():
    return Registry().load_builtins()


def test_reviewer_clears_reviewable_deny(reg):
    ev = evaluate(bash("kubectl apply -f staging.yaml"), reg, CFG_ON, history=_hist(), probe=FakeProbe(),
                  reviewer=_rev(ON_TASK))
    assert ev.decision.kind == "allow"
    r = next(x for x in ev.results if x.policy == "no-kubectl-mutate")
    assert r.reviewer_verdict["cleared"] is True


def test_reviewer_keeps_deny_when_check_says_deny(reg):
    ev = evaluate(bash("kubectl apply -f prod.yaml"), reg, CFG_ON, history=_hist("fix a typo"), probe=FakeProbe(),
                  reviewer=_rev(OFF_TASK))
    assert ev.decision.kind == "deny" and ev.decision.policy == "no-kubectl-mutate"


def test_observe_mode_reviewer_does_not_clear(reg):
    ev = evaluate(bash("kubectl apply -f staging.yaml"), reg, CFG_ON, history=_hist(), probe=FakeProbe(),
                  reviewer=_rev(ON_TASK, mode="observe"))
    assert ev.decision.kind == "deny"
    r = next(x for x in ev.results if x.policy == "no-kubectl-mutate")
    assert r.reviewer_verdict["cleared"] is True and r.reviewer_verdict["mode"] == "observe"


def test_hard_policy_never_cleared(reg):
    ev = evaluate(bash("rm -rf src"), reg, {}, history=_hist("delete src please"), probe=FakeProbe(),
                  reviewer=_rev(ON_TASK))
    assert ev.decision.kind == "deny" and ev.decision.policy == "no-rm-rf"


def test_reviewer_error_policy_stands(reg):
    ev = evaluate(bash("kubectl apply -f x.yaml"), reg, CFG_ON, history=_hist(), probe=FakeProbe(),
                  reviewer=_rev(TimeoutError("slow")))
    assert ev.decision.kind == "deny"


def test_missing_check_fails_closed(reg, monkeypatch):
    p = reg.get("no-kubectl-mutate")
    monkeypatch.setattr(p, "reviewed_by", ["does-not-exist"])
    ev = evaluate(bash("kubectl apply -f x.yaml"), reg, CFG_ON, history=_hist(), probe=FakeProbe(),
                  reviewer=_rev(ON_TASK))
    assert ev.decision.kind == "deny"


def test_warn_still_clears():
    c = CHECKS["on-task"]
    assert verdict_from(c, {**OFF_TASK, "evidence": "moderate"}) == "warn"
    assert verdict_from(c, OFF_TASK) == "deny"
    assert verdict_from(c, {**OFF_TASK, "probes": {"p1": True, "p2": False}}) == "allow"   # all probes must hold


def test_cache_per_session(conn, reg):
    calls = []
    rv = _rev(ON_TASK, calls=calls, conn=conn)
    for _ in range(3):
        evaluate(bash("kubectl apply -f staging.yaml"), reg, CFG_ON, history=_hist(), probe=FakeProbe(), reviewer=rv)
    assert len(calls) == 1
    rv2 = _rev(ON_TASK, calls=calls, conn=conn)       # new process, same db
    evaluate(bash("kubectl apply -f staging.yaml"), reg, CFG_ON, history=_hist(), probe=FakeProbe(), reviewer=rv2)
    assert len(calls) == 1


def test_standalone_check_can_deny(reg):
    check("payments-outside-task", "Touched payments outside the task", applies_to=["write"], mode="deny",
          probes=[{"id": "p1", "instructions": "touches payments?", "criteria": {"true": "yes", "false": "no"}}],
          precondition="has_paths", guidance="Stay on the login task.")
    try:
        ans = {"probes": {"p1": True}, "exempt": False, "evidence": "strong", "rationale": "payments file"}
        ev = evaluate(event("Write", {"file_path": "src/payments/x.py", "content": "a"}), reg, {}, history=_hist("fix login"),
                      probe=FakeProbe(), reviewer=_rev(ans))
        assert ev.decision.kind == "deny" and ev.decision.policy == "check:payments-outside-task"
        ev2 = evaluate(bash("ls"), reg, {}, history=_hist("fix login"), probe=FakeProbe(), reviewer=_rev(ans))
        assert ev2.decision.kind == "allow"     # shell not in applies_to
    finally:
        CHECKS.pop("payments-outside-task", None)


def test_check_validation():
    with pytest.raises(ValueError):
        checks_mod.Check("x", "x", probes=[])
    with pytest.raises(ValueError):
        checks_mod.Check("x", "x", probes=[{"id": "a", "instructions": "?"}], precondition="weird")


def test_preconditions(project):
    from dinoguard.engine.context import PolicyContext
    c = checks_mod.Check("pc", "pc", probes=[{"id": "a", "instructions": "?"}], precondition="paths_outside_project")
    ctx = PolicyContext(event=bash("cat /etc/passwd", cwd=str(project)), probe=FakeProbe())
    assert precondition_holds(c, ctx)
    ctx2 = PolicyContext(event=bash("cat ./a.txt", cwd=str(project)), probe=FakeProbe())
    assert not precondition_holds(c, ctx2)
    c.precondition = "protected_branch"
    assert precondition_holds(c, PolicyContext(event=bash("x"), probe=FakeProbe(branch="main")))
    assert not precondition_holds(c, PolicyContext(event=bash("x"), probe=FakeProbe(branch="feat")))
    c.precondition = "system_or_root_paths"
    assert precondition_holds(c, ctx)
