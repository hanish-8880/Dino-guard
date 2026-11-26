import os

import pytest

from dinoguard.engine.context import History, TokenStats
from helpers import FakeProbe, event, run_policy
from policies.cases import CASES, INSTRUCT_POLICIES


def _build(spec, cwd):
    if isinstance(spec, str):
        spec = {"cmd": spec}
    for f in spec.get("cwd_files", []):
        p = os.path.join(cwd, f)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()
    ev_name = spec.get("ev", "PreToolUse")
    if "cmd" in spec:
        tool, inp = "Bash", {"command": spec["cmd"]}
    else:
        tool, inp = spec.get("tool"), spec.get("input")
    ev = event(tool, inp, ev=ev_name, cwd=cwd, output=spec.get("output"), output_chars=spec.get("output_chars"))
    hist = History(spec.get("history", []), first_prompt=spec.get("prompt"),
                   prompts=[spec["prompt"]] if spec.get("prompt") else [])
    probe = FakeProbe(branch=spec.get("branch"), git=spec.get("git"), gh=spec.get("gh"),
                      in_repo=spec.get("in_repo", True))
    tokens = TokenStats(**spec.get("tokens", {}))
    return spec, ev, hist, probe, tokens


def _params():
    for name, groups in CASES.items():
        for i, spec in enumerate(groups["fire"]):
            yield pytest.param(name, spec, True, id=f"{name}-fire-{i}")
        for i, spec in enumerate(groups["allow"]):
            yield pytest.param(name, spec, False, id=f"{name}-allow-{i}")


@pytest.mark.parametrize("name,spec,should_fire", list(_params()))
def test_case(builtins, project, name, spec, should_fire):
    p = builtins.get(name)
    assert p is not None, f"no built-in {name}"
    spec, ev, hist, probe, tokens = _build(spec, str(project))
    d = run_policy(p, ev, history=hist, params=spec.get("params"), probe=probe, tokens=tokens)
    if should_fire:
        expected = spec.get("kind") or ("instruct" if name in INSTRUCT_POLICIES else "deny")
        assert d.kind == expected, f"{name} expected {expected} for {spec}, got {d.kind}: {d.reason}"
        assert d.reason, "reasons are required for the agent"
    else:
        assert d.kind == "allow", f"{name} should allow {spec}, got {d.kind}: {d.reason}"


def test_every_builtin_has_cases(builtins):
    names = {p.name for p in builtins.policies}
    missing = names - set(CASES)
    assert not missing, f"built-ins without test cases: {sorted(missing)}"
    for name, groups in CASES.items():
        assert len(groups["fire"]) >= 2 and len(groups["allow"]) >= 2, name


def test_about_fifty_builtins(builtins):
    assert 45 <= len(builtins.policies) <= 60


def test_reviewable_builtins_have_reviewers(builtins):
    for p in builtins.policies:
        if p.authority == "reviewable":
            assert p.reviewed_by, p.name


def test_default_enabled_set(builtins):
    enabled = {p.name for p in builtins.policies if p.default_enabled}
    assert {"guard-self-protect", "secret-in-output", "secret-before-write", "no-env-read", "no-sudo", "no-rm-rf",
            "no-curl-pipe-shell", "no-push-main", "no-force-push", "warn-destructive-sql", "loop-detected",
            # token tips are instruct-only, so they are on by default; token-budget (can deny) stays off
            "token-repeat-read", "token-big-output", "token-context-high", "token-model-fit"} == enabled
