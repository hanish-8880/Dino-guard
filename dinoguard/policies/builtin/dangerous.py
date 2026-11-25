"""Dangerous shell commands."""
from __future__ import annotations

import re

from ...engine.decisions import allow, deny
from ...engine.registry import builtin_policy
from . import _shell
from ._common import bash_cmd, segs

_FETCHERS = {"curl", "wget", "iwr", "invoke-webrequest", "irm", "invoke-restmethod", "fetch", "http", "aria2c"}
_INTERPS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "python", "python3", "perl", "ruby", "node", "iex",
            "invoke-expression", "pwsh", "powershell", "php", "deno", "bun"}
_PROC_SUBST = re.compile(r"(?i)\b(?:ba|z|da|k)?sh\s+<\(\s*(?:curl|wget)\b")
_EVAL_FETCH = re.compile(r"(?i)(?:\beval|\b(?:ba|z)?sh\s+-c|\biex|invoke-expression)\s*[\"'(]?\s*\$?\(?\s*(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod|\(?new-object\s+net\.webclient)")


@builtin_policy("no-sudo", category="dangerous", description="Deny running commands as root (sudo, doas, su -c, runas).")
def no_sudo(ctx):
    for s in segs(ctx):
        w = s.words
        names = [_shell.cmd_name(x) for x in w[:3]]
        names = [n for n in names if "=" not in n]
        if names and (any(n in ("sudo", "doas", "gsudo", "pkexec") for n in names) or names[0] == "runas"
                      or (names[0] == "su" and ("-c" in w or "-" in w or len(w) == 1))):
            return deny("Running commands with elevated privileges is blocked. Use a user-level alternative "
                        "(e.g. a virtualenv, `npm install` without -g, a project-local tool) or ask the user to "
                        "run the privileged step themselves.")
    if re.search(r"(?i)start-process\b.*-verb\s+runas", bash_cmd(ctx)):
        return deny("Elevating to administrator is blocked. Ask the user to run the privileged step themselves.")
    return allow()


def _rm_rf(a: list[str]) -> bool:
    name = _shell.cmd_name(a[0])
    flags = [w for w in a[1:] if w.startswith("-")]
    if name == "rm":
        joined = "".join(f[1:] for f in flags if not f.startswith("--"))
        rec = "r" in joined.lower() or "--recursive" in flags
        force = "f" in joined or "--force" in flags
        return rec and force
    if name in ("remove-item", "ri", "del", "erase", "rd", "rmdir"):
        low = [f.lower() for f in a[1:]]
        ps_rec = any(f.startswith("-r") for f in low if f.startswith("-"))
        ps_force = any(f.startswith("-fo") for f in low)
        cmd_rec = "/s" in low
        cmd_quiet = "/q" in low
        return (ps_rec and ps_force) or (cmd_rec and cmd_quiet)
    return False


def _targets(a: list[str]) -> list[str]:
    return [w for w in a[1:] if not w.startswith("-") and not w.startswith("/")
            or w.startswith("/") and len(w) > 2]


@builtin_policy("no-rm-rf", category="dangerous", params={"allow_targets": []},
                description="Deny recursive force deletes (rm -rf, Remove-Item -Recurse -Force, rd /s /q).")
def no_rm_rf(ctx):
    allow_targets = set(ctx.params.get("allow_targets") or [])
    for s in segs(ctx):
        a = s.argv
        if not a or not _rm_rf(a):
            continue
        t = [x.rstrip("/\\") for x in _targets(a)]
        if t and allow_targets and all(x in allow_targets for x in t):
            continue
        return deny(f"Recursive force delete is blocked (`{s.text[:80]}`). Delete specific files by name, move "
                    "the directory aside (`mv dir dir.bak`), or ask the user to run the delete.")
    return allow()


@builtin_policy("no-curl-pipe-shell", category="dangerous",
                description="Deny piping downloaded content straight into a shell or interpreter.")
def no_curl_pipe_shell(ctx):
    cmd = bash_cmd(ctx)
    if not cmd:
        return allow()
    reason = ("Piping a download straight into a shell runs unreviewed remote code. Download to a file first, "
              "show the user what it does, and install through a package manager where possible.")
    if _PROC_SUBST.search(cmd) or _EVAL_FETCH.search(cmd):
        return deny(reason)
    ss = _shell.segments(cmd)
    for i, s in enumerate(ss[:-1]):
        if s.cmd in _FETCHERS and s.sep_after == "|":
            j = i + 1
            while j < len(ss) and ss[j].cmd in ("tee", "cat", "sed", "tr", "head") and ss[j].sep_after == "|":
                j += 1
            if j < len(ss) and ss[j].cmd in _INTERPS:
                return deny(reason)
    return allow()


@builtin_policy("no-chmod-777", category="dangerous", default_enabled=False, authority="reviewable",
                reviewed_by=["on-task"], description="Deny world-writable permissions (chmod 777, a+rwx).")
def no_chmod_777(ctx):
    for s in segs(ctx):
        a = s.argv
        if not a:
            continue
        if s.cmd == "chmod" and any(re.fullmatch(r"0?777|[augo]*\+rwx|a=rwx|ugo=rwx|o\+w", w) for w in a[1:]):
            return deny("World-writable permissions are blocked. Grant only what is needed, e.g. `chmod 755` for "
                        "scripts or `chmod 644` for files.")
        if s.cmd == "icacls" and re.search(r"(?i)everyone:\(?[^\s]*F", s.text):
            return deny("Granting Everyone full control is blocked. Grant the specific user only what they need.")
    return allow()


_DISK = [
    re.compile(r"(?i)(?:^|\s|/)mkfs(?:\.\w+)?\b"),
    re.compile(r"(?i)\bdd\b[^\n]*\bof=/dev/(?!null\b|zero\b|stdout\b)"),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
    re.compile(r"(?i)>\s*/dev/(?:sd[a-z]|nvme\d|disk\d|hd[a-z]|mmcblk\d)"),
    re.compile(r"(?i)\b(?:shred|wipefs|blkdiscard)\b[^\n]*/dev/"),
    re.compile(r"(?i)\bformat(?:\.com)?\s+[a-z]:"),
    re.compile(r"(?i)\b(?:diskpart|format-volume|clear-disk|initialize-disk|remove-partition)\b"),
    re.compile(r"(?i)\brm\s+(?:-[a-z]+\s+)*(?:--no-preserve-root|/\s*$|/\*)"),
]


@builtin_policy("no-disk-destroy", category="dangerous", default_enabled=False,
                description="Deny disk-destroying commands (mkfs, dd to devices, fork bombs, format).")
def no_disk_destroy(ctx):
    cmd = bash_cmd(ctx)
    if cmd and any(rx.search(cmd) for rx in _DISK):
        return deny("This command can destroy a disk or hang the machine and is never allowed from the agent.")
    return allow()
