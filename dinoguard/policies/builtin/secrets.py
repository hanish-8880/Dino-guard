"""Secret handling: in tool output, before writes, private key files."""
from __future__ import annotations

import fnmatch
import re

from ...engine.decisions import allow, deny, instruct
from ...engine.registry import builtin_policy
from ...secrets.scanner import kinds
from . import _shell
from ._common import basename, bash_cmd, file_path, segs, written_content

KEY_NAMES = ["id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks",
             "*.keystore", "*.ppk"]


@builtin_policy("secret-in-output", events=["PostToolUse", "PostToolUseFailure"], category="secrets",
                description="Detect secrets in tool output; redact them from logs and tell the agent not to repeat them.")
def secret_in_output(ctx):
    found = kinds(ctx.event.output)
    if not found:
        return allow()
    return instruct(
        f"The last tool output contained what looks like a secret ({', '.join(found)}). DinoGuard redacted it "
        "from its logs. Do not repeat, print, summarise or write this value anywhere; refer to it by the name "
        "of the environment variable or config key instead.")


def _bash_writes_secret(ctx) -> list[str]:
    cmd = bash_cmd(ctx)
    if not cmd:
        return []
    writes = _shell.is_heredoc(cmd) or any(_shell.written_files(s) for s in segs(ctx))
    if not writes:
        return []
    return kinds(cmd)


@builtin_policy("secret-before-write", category="secrets",
                description="Block writing secrets into files (Write/Edit content, shell redirects and heredocs).")
def secret_before_write(ctx):
    if ctx.tool_name in ("Write", "Edit"):
        found = kinds(written_content(ctx))
    elif ctx.tool_name == "Bash":
        found = _bash_writes_secret(ctx)
    else:
        return allow()
    if not found:
        return allow()
    return deny(
        f"This write contains a secret ({', '.join(found)}). Never hard-code credentials in files. Read the value "
        "from an environment variable (e.g. os.environ['API_KEY'] / process.env.API_KEY) and document the "
        "variable name in .env.example instead.")


def _is_key_file(p: str, patterns: list[str]) -> bool:
    b = basename(p).lower()
    return any(fnmatch.fnmatch(b, pat.lower()) for pat in patterns)


@builtin_policy("no-private-key-write", category="secrets", default_enabled=False,
                params={"patterns": KEY_NAMES},
                description="Deny writing private key files such as id_rsa, *.pem, *.key.")
def no_private_key_write(ctx):
    pats = ctx.params.get("patterns") or KEY_NAMES
    targets: list[str] = []
    if ctx.tool_name in ("Write", "Edit"):
        targets = [file_path(ctx)]
    elif ctx.tool_name == "Bash":
        for s in segs(ctx):
            targets.extend(_shell.written_files(s))
            if s.cmd in ("ssh-keygen",):
                a = s.argv
                if "-f" in a and a.index("-f") + 1 < len(a):
                    targets.append(a[a.index("-f") + 1])
            if s.cmd == "openssl" and re.search(r"\bgen(?:rsa|pkey)\b", s.text):
                a = s.argv
                if "-out" in a and a.index("-out") + 1 < len(a):
                    targets.append(a[a.index("-out") + 1])
    hits = [t for t in targets if t and _is_key_file(t, pats)]
    if not hits:
        return allow()
    return deny(f"Writing private key material ({', '.join(hits)}) is not allowed from the agent. Ask the user to "
                "create or install keys themselves, and keep keys out of the repository.")
