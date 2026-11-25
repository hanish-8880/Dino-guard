"""Git safety."""
from __future__ import annotations

from ...engine.decisions import allow, deny
from ...engine.registry import builtin_policy
from ._common import git_commands

PROTECTED = ["main", "master"]
_REVIEW = dict(authority="reviewable", reviewed_by=["on-task"], default_enabled=False)


def _push_refspecs(g: list[str]) -> tuple[list[str], list[str]]:
    """Split `git push` args into (flags, positionals)."""
    flags = [w for w in g[2:] if w.startswith("-")]
    pos = [w for w in g[2:] if not w.startswith("-")]
    return flags, pos


def _ref_targets(refspec: str) -> str:
    r = refspec.lstrip("+")
    dst = r.split(":", 1)[1] if ":" in r else r
    return dst.replace("refs/heads/", "")


@builtin_policy("no-push-main", category="git", params={"protected": PROTECTED},
                description="Deny pushing directly to main/master.")
def no_push_main(ctx):
    prot = set(ctx.params.get("protected") or PROTECTED)
    for g in git_commands(ctx, "push"):
        flags, pos = _push_refspecs(g)
        refspecs = pos[1:]  # first positional is the remote
        targets = {_ref_targets(r) for r in refspecs}
        if "--delete" in flags or "-d" in flags:
            continue
        if not refspecs or targets & {"HEAD"} or "--all" in flags or "--mirror" in flags:
            branch = ctx.probe.branch(ctx.cwd) if ctx.probe else None
            if branch in prot or "--mirror" in flags:
                return deny(f"You are on '{branch}', and pushing it directly is blocked. Create a feature branch "
                            "(`git switch -c my-change`), push that, and open a pull request.")
        hit = targets & prot
        if hit:
            return deny(f"Pushing directly to '{sorted(hit)[0]}' is blocked. Push a feature branch and open a "
                        "pull request instead.")
    return allow()


@builtin_policy("no-force-push", category="git", description="Deny force pushes (-f, --force, --force-with-lease, +refspec).")
def no_force_push(ctx):
    for g in git_commands(ctx, "push"):
        flags, pos = _push_refspecs(g)
        forced = any(f in ("-f", "--force", "--force-with-lease", "--force-if-includes") or
                     f.startswith("--force-with-lease=") or (f.startswith("-") and not f.startswith("--") and "f" in f[1:])
                     for f in flags)
        if forced or any(p.startswith("+") for p in pos[1:]):
            return deny("Force pushing rewrites shared history and is blocked. Push new commits on top instead "
                        "(`git pull --rebase` then `git push`), or ask the user to force push if truly needed.")
    return allow()


@builtin_policy("no-commit-on-main", category="git", params={"protected": PROTECTED}, **_REVIEW,
                description="Deny committing while on main/master.")
def no_commit_on_main(ctx):
    prot = set(ctx.params.get("protected") or PROTECTED)
    if git_commands(ctx, "commit"):
        branch = ctx.probe.branch(ctx.cwd) if ctx.probe else None
        if branch in prot:
            return deny(f"Committing directly on '{branch}' is blocked. Create a branch first: "
                        "`git switch -c <short-topic-name>`, then commit.")
    return allow()


@builtin_policy("no-amend-pushed", category="git", **_REVIEW,
                description="Deny amending a commit that has already been pushed.")
def no_amend_pushed(ctx):
    for g in git_commands(ctx, "commit"):
        if "--amend" not in g:
            continue
        rc, out = ctx.probe.git(ctx.cwd, "branch", "-r", "--contains", "HEAD")
        if rc == 0 and out.strip():
            return deny("HEAD is already pushed, so amending it would rewrite shared history. Make a new commit "
                        "with the fix instead.")
    return allow()


@builtin_policy("no-stash-drop", category="git", **_REVIEW, description="Deny git stash drop / clear.")
def no_stash_drop(ctx):
    for g in git_commands(ctx, "stash"):
        if len(g) > 2 and g[2] in ("drop", "clear"):
            return deny(f"`git stash {g[2]}` permanently deletes saved work. Leave the stash in place and tell the "
                        "user it exists; they can drop it themselves.")
    return allow()


@builtin_policy("no-git-add-all", category="git", **_REVIEW, description="Deny `git add -A` / `git add .`.")
def no_git_add_all(ctx):
    for g in git_commands(ctx, "add"):
        if any(w in ("-A", "--all", ".", "*", ":/", "--no-ignore-removal") for w in g[2:]):
            return deny("Adding everything can commit secrets, build output or unrelated changes. Add the files "
                        "you changed by name: `git add path/to/file ...` (check `git status` first).")
    return allow()


@builtin_policy("no-git-clean", category="git", **_REVIEW, description="Deny `git clean -f` (deletes untracked files).")
def no_git_clean(ctx):
    for g in git_commands(ctx, "clean"):
        flags = "".join(w[1:] for w in g[2:] if w.startswith("-") and not w.startswith("--"))
        if ("f" in flags or "--force" in g) and "n" not in flags and "--dry-run" not in g:
            return deny("`git clean -f` permanently deletes untracked files. Run `git clean -n` to list them and "
                        "ask the user before deleting.")
    return allow()
