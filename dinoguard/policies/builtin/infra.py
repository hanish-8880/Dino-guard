"""Infrastructure mutations: kubectl, terraform, cloud CLIs, helm, CI triggers."""
from __future__ import annotations

import re

from ...engine.decisions import allow, deny
from ...engine.registry import builtin_policy
from ._common import segs

_REVIEW = dict(authority="reviewable", reviewed_by=["on-task"], default_enabled=False, category="infrastructure")

KUBECTL_MUTATE = {"apply", "delete", "scale", "patch", "replace", "create", "edit", "drain", "cordon",
                  "uncordon", "taint", "set", "annotate", "label", "expose", "autoscale"}
_AWS_PREFIX = ("create", "delete", "update", "put", "remove", "terminate", "modify", "run-", "stop", "start",
               "reboot", "attach", "detach", "deregister", "register", "revoke", "authorize", "set", "disable",
               "enable", "import", "restore", "invoke", "publish", "send", "tag", "untag")
_CLOUD_WORDS = {"create", "delete", "update", "deploy", "set", "add", "remove", "destroy", "start", "stop",
                "restart", "resize", "patch", "import", "rollback", "purge", "upgrade", "apply"}


def _positional(argv: list[str]) -> list[str]:
    out, skip = [], False
    for w in argv[1:]:
        if skip:
            skip = False
            continue
        if w.startswith("-"):
            if "=" not in w and w in ("-n", "--namespace", "--context", "-f", "--profile", "--region",
                                      "--project", "-g", "--resource-group", "--kubeconfig", "-o"):
                skip = True
            continue
        out.append(w)
    return out


@builtin_policy("no-kubectl-mutate", description="Deny kubectl commands that change a cluster.", **_REVIEW)
def no_kubectl_mutate(ctx):
    for s in segs(ctx):
        if s.cmd not in ("kubectl", "oc", "k"):
            continue
        pos = _positional(s.argv)
        if pos and (pos[0] in KUBECTL_MUTATE or (pos[0] == "rollout" and len(pos) > 1 and pos[1] in ("restart", "undo"))):
            return deny(f"`kubectl {pos[0]}` changes a live cluster. Show the user the manifest/diff "
                        "(`kubectl diff -f ...`) and let them apply it.")
    return allow()


@builtin_policy("no-terraform-apply", description="Deny terraform/tofu apply and destroy.", **_REVIEW)
def no_terraform_apply(ctx):
    for s in segs(ctx):
        if s.cmd not in ("terraform", "tofu", "terragrunt"):
            continue
        pos = _positional(s.argv)
        if pos and (pos[0] in ("apply", "destroy", "import") or pos[:2] == ["state", "rm"]
                    or pos[:2] == ["workspace", "delete"] or pos[:2] == ["run-all", "apply"]):
            return deny(f"`{s.cmd} {' '.join(pos[:2])}` changes real infrastructure. Run `{s.cmd} plan` and "
                        "share the plan with the user instead.")
    return allow()


@builtin_policy("no-cloud-cli-mutate", description="Deny aws/gcloud/az commands that create, change or delete resources.", **_REVIEW)
def no_cloud_cli_mutate(ctx):
    for s in segs(ctx):
        pos = _positional(s.argv)
        if s.cmd == "aws" and len(pos) >= 2:
            if pos[0] == "s3" and pos[1] in ("rm", "mv", "rb", "sync", "cp", "mb"):
                return deny(f"`aws s3 {pos[1]}` changes cloud storage. Show the user the command to run.")
            if pos[1].startswith(_AWS_PREFIX):
                return deny(f"`aws {pos[0]} {pos[1]}` changes cloud resources. Use describe/list/get commands, "
                            "and let the user run mutations.")
        if s.cmd in ("gcloud", "az", "doctl", "flyctl", "fly", "heroku", "vercel", "netlify") and \
                any(w in _CLOUD_WORDS for w in pos):
            return deny(f"`{s.cmd} {' '.join(pos[:3])}` looks like it changes cloud resources. Use read-only "
                        "commands (list/describe/show) and let the user run the change.")
    return allow()


@builtin_policy("no-helm-mutate", description="Deny helm install/upgrade/uninstall/rollback.", **_REVIEW)
def no_helm_mutate(ctx):
    for s in segs(ctx):
        if s.cmd != "helm":
            continue
        pos = _positional(s.argv)
        if pos and pos[0] in ("install", "upgrade", "uninstall", "delete", "rollback", "del", "un"):
            return deny(f"`helm {pos[0]}` changes a live cluster. Use `helm template` / `helm diff` and let the "
                        "user apply the release.")
    return allow()


@builtin_policy("no-ci-trigger", description="Deny triggering CI runs (gh workflow run, gh run rerun).", **_REVIEW)
def no_ci_trigger(ctx):
    for s in segs(ctx):
        if s.cmd != "gh":
            continue
        pos = _positional(s.argv)
        if pos[:2] in (["workflow", "run"], ["run", "rerun"], ["workflow", "enable"], ["workflow", "disable"]) \
                or (pos[:1] == ["api"] and re.search(r"/dispatches\b", s.text)):
            return deny("Triggering CI from the agent is blocked. Push your branch and let CI run normally, or ask "
                        "the user to trigger the workflow.")
    return allow()
