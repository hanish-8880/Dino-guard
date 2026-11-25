"""Turn-end gates: block Stop / SubagentStop until the workflow is complete. All off by default."""
from __future__ import annotations

import json

from ...engine.decisions import allow, deny
from ...engine.registry import builtin_policy
from .ai_behaviour import TEST_CMD

_GATE = dict(events=["Stop", "SubagentStop"], category="workflow", default_enabled=False)


def _in_repo(ctx) -> bool:
    rc, _ = ctx.probe.git(ctx.cwd, "rev-parse", "--is-inside-work-tree")
    return rc == 0


@builtin_policy("stop-require-commit", description="Block ending the turn with uncommitted changes.", **_GATE)
def stop_require_commit(ctx):
    if not _in_repo(ctx):
        return allow()
    rc, out = ctx.probe.git(ctx.cwd, "status", "--porcelain")
    if rc == 0 and out.strip():
        n = len(out.strip().splitlines())
        return deny(f"There are {n} uncommitted change(s). Not done yet: review `git status`, commit the work "
                    "with a clear message, then finish.")
    return allow()


@builtin_policy("stop-require-push", description="Block ending the turn with unpushed commits.", **_GATE)
def stop_require_push(ctx):
    if not _in_repo(ctx):
        return allow()
    rc, out = ctx.probe.git(ctx.cwd, "rev-list", "--count", "@{u}..HEAD")
    if rc != 0:
        return deny("This branch has no upstream. Not done yet: push it with `git push -u origin HEAD`, then finish.")
    if out.strip().isdigit() and int(out.strip()) > 0:
        return deny(f"{out.strip()} commit(s) are not pushed. Not done yet: `git push`, then finish.")
    return allow()


@builtin_policy("stop-require-pr", description="Block ending the turn until the branch has a pull request.", **_GATE)
def stop_require_pr(ctx):
    if not _in_repo(ctx):
        return allow()
    rc, out = ctx.probe.gh(ctx.cwd, "pr", "view", "--json", "state")
    if rc != 0 or not out:
        return deny("No pull request exists for this branch. Not done yet: open one with `gh pr create --fill`, "
                    "then finish.")
    return allow()


@builtin_policy("stop-require-no-conflicts", description="Block ending the turn with unresolved merge conflicts.", **_GATE)
def stop_require_no_conflicts(ctx):
    if not _in_repo(ctx):
        return allow()
    rc, out = ctx.probe.git(ctx.cwd, "diff", "--name-only", "--diff-filter=U")
    if rc == 0 and out.strip():
        files = ", ".join(out.split()[:5])
        return deny(f"Unresolved merge conflicts remain in: {files}. Not done yet: resolve them, then finish.")
    return allow()


@builtin_policy("stop-require-green-ci", description="Block ending the turn until CI for this branch is green.", **_GATE)
def stop_require_green_ci(ctx):
    if not _in_repo(ctx):
        return allow()
    branch = ctx.probe.branch(ctx.cwd)
    rc, out = ctx.probe.gh(ctx.cwd, "run", "list", "--branch", branch or "HEAD", "--limit", "1",
                           "--json", "status,conclusion")
    if rc != 0:
        return allow()   # gh unavailable: cannot judge, do not trap the agent
    try:
        runs = json.loads(out or "[]")
    except ValueError:
        return allow()
    if not runs:
        return deny("No CI run found for this branch yet. Not done yet: push and wait for CI, then finish.")
    run = runs[0]
    if run.get("status") != "completed":
        return deny("CI is still running. Not done yet: wait for it (`gh run watch`), then finish.")
    if run.get("conclusion") != "success":
        return deny(f"The latest CI run concluded '{run.get('conclusion')}'. Not done yet: inspect it "
                    "(`gh run view --log-failed`), fix it, push, then finish.")
    return allow()


@builtin_policy("stop-require-tests-pass", description="Block ending the turn if code changed after the last passing test run.", **_GATE)
def stop_require_tests_pass(ctx):
    items = list(ctx.history)
    last_edit = max((i for i, h in enumerate(items) if h.get("event") == "PostToolUse"
                     and h.get("tool_name") in ("Write", "Edit")), default=None)
    if last_edit is None:
        return allow()
    last_pass = None
    last_fail = None
    for i, h in enumerate(items):
        if h.get("tool_name") == "Bash" and TEST_CMD.search(str((h.get("input") or {}).get("command", ""))):
            if h.get("event") == "PostToolUse" and not h.get("error"):
                last_pass = i
            elif h.get("event") in ("PostToolUseFailure",) or (h.get("event") == "PostToolUse" and h.get("error")):
                last_fail = i
    if last_pass is not None and last_pass > last_edit and (last_fail is None or last_fail < last_pass):
        return allow()
    return deny("Code changed since the last passing test run. Not done yet: run the test suite, fix any failures, "
                "then finish.")
