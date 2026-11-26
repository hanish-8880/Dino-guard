"""Environment files and variables."""
from __future__ import annotations

import re

from ...engine.decisions import allow, deny
from ...engine.registry import builtin_policy
from ...util import is_within, norm_path
from . import _shell
from ._common import bash_cmd, file_path, is_env_file, segs

READERS = {"cat", "less", "more", "head", "tail", "bat", "type", "get-content", "gc", "grep", "rg", "egrep",
           "fgrep", "awk", "sed", "source", ".", "cp", "copy", "xxd", "od", "strings", "sort", "uniq", "nl",
           "vim", "vi", "nano", "code", "base64", "python", "python3", "node", "ruby", "perl", "jq", "dotenv",
           "select-string", "sls", "export", "set", "tac", "cut", "paste", "column", "scp", "rsync", "curl"}


def _env_tokens(text_words: list[str]) -> list[str]:
    out = []
    for w in text_words:
        for part in re.split(r"[=:,]", w):
            if part and is_env_file(part.strip("'\"<>()")):
                out.append(part)
    return out


@builtin_policy("no-env-read", category="environment",
                description="Deny reading .env files (templates like .env.example are fine).")
def no_env_read(ctx):
    if ctx.tool_name in ("Read", "Grep", "Glob"):
        p = file_path(ctx) if ctx.tool_name == "Read" else str(ctx.tool_input.get("path") or "")
        pat = str(ctx.tool_input.get("pattern") or "") if ctx.tool_name == "Glob" else ""
        if (p and is_env_file(p)) or (pat and is_env_file(pat)):
            return deny("Reading .env files is blocked: they hold real secrets. Read .env.example to learn which "
                        "variables exist, or ask the user for the specific non-secret value you need.")
        return allow()
    if ctx.tool_name != "Bash":
        return allow()
    for s in segs(ctx):
        a = s.argv
        if not a:
            continue
        name = s.cmd
        hits = _env_tokens(a[1:])
        if re.search(r"<\s*\S*\.env\b", s.text) and not re.search(r"\.env\.(example|sample|template)", s.text):
            hits.append(".env")
        if hits and (name in READERS or name.startswith("python") or re.search(r"<\s*\S*\.env", s.text)):
            return deny(f"Reading {hits[0]} is blocked: .env files hold real secrets. Use .env.example to see "
                        "the variable names, or ask the user for the value you need.")
    return allow()


@builtin_policy("no-env-write", category="environment", default_enabled=False,
                description="Deny writing .env files; edit .env.example and let the user fill real values.")
def no_env_write(ctx):
    targets: list[str] = []
    if ctx.tool_name in ("Write", "Edit"):
        targets = [file_path(ctx)]
    elif ctx.tool_name == "Bash":
        for s in segs(ctx):
            targets.extend(_shell.written_files(s))
            if s.cmd in ("cp", "mv", "copy", "move", "copy-item", "move-item") and len(s.argv) >= 3:
                targets.append(s.argv[-1])
    hits = [t for t in targets if t and is_env_file(t)]
    if hits:
        return deny(f"Writing {hits[0]} is blocked. Put variable names (no real values) in .env.example and ask "
                    "the user to set real values themselves.")
    return allow()


_PS_ENV = re.compile(r"(?i)\b(?:get-childitem|gci|dir|ls|get-item)\s+env:")


@builtin_policy("no-env-dump", category="environment", default_enabled=False, authority="reviewable",
                reviewed_by=["on-task"],
                description="Deny dumping all environment variables (env, printenv, set, export -p).")
def no_env_dump(ctx):
    cmd = bash_cmd(ctx)
    if not cmd:
        return allow()
    reason = ("Dumping every environment variable can expose secrets. Print only the specific variable you "
              "need, e.g. `echo \"$PATH\"`, or check existence with `test -n \"$VAR\"`.")
    if _PS_ENV.search(cmd) or "/proc/self/environ" in cmd or re.search(r"/proc/\d+/environ", cmd):
        return deny(reason)
    for s in segs(ctx):
        raw = s.words
        if raw and _shell.cmd_name(raw[0]) == "env" and all(w.startswith("-") for w in raw[1:]):
            return deny(reason)   # bare `env` (env-with-a-command is fine)
        a = s.argv
        name = s.cmd
        rest = list(a[1:])
        if name == "printenv" and not [w for w in rest if not w.startswith("-")]:
            return deny(reason)
        if name == "set" and not rest:
            return deny(reason)
        if name == "export" and (not rest or rest == ["-p"]):
            return deny(reason)
        if name == "declare" and rest and rest[0] in ("-x", "-p", "-px"):
            return deny(reason)
    return allow()


@builtin_policy("no-read-outside-project", category="environment", default_enabled=False,
                authority="reviewable", reviewed_by=["on-task"], params={"allow": ["~/.claude", "/tmp"]},
                description="Deny reading files outside the project directory (allowlist configurable).")
def no_read_outside_project(ctx):
    cwd = ctx.cwd
    allowed = [norm_path(a) for a in ctx.params.get("allow", [])]
    if "/tmp" in ctx.params.get("allow", []):           # "/tmp" means the OS temp folder everywhere (%TEMP% on Windows)
        import tempfile
        allowed.append(norm_path(tempfile.gettempdir()))
    candidates: list[str] = []
    if ctx.tool_name in ("Read", "Grep", "Glob"):
        p = file_path(ctx) if ctx.tool_name == "Read" else str(ctx.tool_input.get("path") or "")
        if p:
            candidates.append(p)
    elif ctx.tool_name == "Bash":
        for s in segs(ctx):
            if s.cmd in READERS:
                candidates.extend(w for w in s.argv[1:] if w.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", w))
    for c in candidates:
        full = norm_path(c, cwd)
        if is_within(full, norm_path(cwd)):
            continue
        if any(is_within(full, a) for a in allowed):
            continue
        return deny(f"{c} is outside the project ({cwd}). Stay inside the project, or ask the user to add the "
                    "location to the no-read-outside-project allowlist.")
    return allow()
