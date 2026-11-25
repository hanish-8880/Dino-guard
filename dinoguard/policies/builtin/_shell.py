"""Quote-aware shell command splitting shared by built-in policies.

Deliberately conservative: nested command substitutions and `sh -c "..."` bodies are
expanded into extra segments so policies see the inner commands too.
"""
from __future__ import annotations

import functools
import os
import re
import shlex
from dataclasses import dataclass

_SEPS = ("&&", "||", ";", "|", "\n", "&")
_SUBST = re.compile(r"\$\(([^()]*(?:\([^()]*\)[^()]*)*)\)|`([^`]*)`")
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "pwsh", "powershell", "cmd"}
_WRAPPERS = {"sudo", "doas", "env", "nohup", "time", "command", "exec", "nice", "ionice",
             "stdbuf", "timeout", "xargs", "setsid", "builtin", "caffeinate"}
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


@dataclass
class Segment:
    text: str
    sep_before: str      # separator that preceded this segment ("" for the first)
    sep_after: str       # separator that followed it ("" for the last)

    @functools.cached_property
    def words(self) -> list[str]:
        return words(self.text)

    @functools.cached_property
    def argv(self) -> list[str]:
        return strip_wrappers(self.words)

    @functools.cached_property
    def cmd(self) -> str:
        a = self.argv
        return cmd_name(a[0]) if a else ""


def cmd_name(word: str) -> str:
    base = os.path.basename(word.replace("\\", "/")).lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1"):
        if base.endswith(ext):
            base = base[: -len(ext)]
    return base


def words(text: str) -> list[str]:
    try:
        lex = shlex.shlex(text, posix=True)
        lex.whitespace_split = True
        lex.escape = ""        # keep Windows backslashes intact
        lex.commenters = ""
        return list(lex)
    except ValueError:
        return text.split()


def strip_wrappers(argv: list[str]) -> list[str]:
    """Drop env assignments and wrappers like sudo/env/nohup to find the real command."""
    i = 0
    while i < len(argv):
        w = argv[i]
        if _ASSIGN.match(w):
            i += 1
            continue
        name = cmd_name(w)
        if name in _WRAPPERS:
            i += 1
            # skip wrapper flags (sudo -u root, timeout 10, env -i)
            while i < len(argv) and (argv[i].startswith("-") or (name == "timeout" and argv[i][:1].isdigit())):
                if name == "sudo" and argv[i] in ("-u", "-g") and i + 1 < len(argv):
                    i += 1
                i += 1
            continue
        break
    return argv[i:]


def _split_top(cmd: str) -> list[tuple[str, str]]:
    """Split on separators outside quotes. Returns [(segment, separator_after)]."""
    out, buf, q, i = [], [], None, 0
    while i < len(cmd):
        c = cmd[i]
        if q:
            buf.append(c)
            if c == q:
                q = None
            i += 1
            continue
        if c in ("'", '"'):
            q = c
            buf.append(c)
            i += 1
            continue
        matched = None
        for s in _SEPS:
            if cmd.startswith(s, i):
                if s == "&" and ((i > 0 and cmd[i - 1] in "><") or cmd.startswith("&>", i)):
                    continue
                if s == "|" and i > 0 and cmd[i - 1] == ">":
                    continue
                matched = s
                break
        if matched:
            out.append(("".join(buf), matched))
            buf = []
            i += len(matched)
            continue
        buf.append(c)
        i += 1
    out.append(("".join(buf), ""))
    return out


def segments(cmd: str) -> list[Segment]:
    """Parsed segments of a command (cached: every policy asks about the same command)."""
    return list(_segments_cached(cmd or ""))


@functools.lru_cache(maxsize=512)
def _segments_cached(cmd: str) -> tuple[Segment, ...]:
    return tuple(_segments(cmd))


def _segments(cmd: str, _depth: int = 0) -> list[Segment]:
    if not cmd:
        return []
    raw = _split_top(cmd)
    segs: list[Segment] = []
    prev = ""
    for text, sep in raw:
        if text.strip():
            segs.append(Segment(text.strip(), prev, sep))
        prev = sep
    if _depth >= 3:
        return segs
    extra: list[Segment] = []
    for s in list(segs):
        for m in _SUBST.finditer(s.text):
            inner = m.group(1) or m.group(2) or ""
            extra.extend(_segments(inner, _depth + 1))
        argv = s.argv
        if argv and cmd_name(argv[0]) in _SHELLS:
            for j, w in enumerate(argv[1:], 1):
                combined_c = (w.startswith("-") and not w.startswith("--") and "c" in w[1:]
                              and cmd_name(argv[0]) not in ("pwsh", "powershell", "cmd"))   # bash -lc, sh -ec
                if (w.lower() in ("-c", "-command", "/c", "-encodedcommand") or combined_c) and j + 1 < len(argv):
                    extra.extend(_segments(" ".join(argv[j + 1:]), _depth + 1))
                    break
        if argv and cmd_name(argv[0]) == "eval":
            extra.extend(_segments(" ".join(argv[1:]), _depth + 1))
    return segs + extra


def commands(cmd: str) -> list[str]:
    return [s.cmd for s in segments(cmd)]


def has_flag(argv: list[str], short: str | None = None, long: str | None = None) -> bool:
    for w in argv[1:]:
        if long and (w == long or w.startswith(long + "=")):
            return True
        if short and w.startswith("-") and not w.startswith("--") and short in w[1:]:
            return True
    return False


_REDIRECT = re.compile(r"(?:^|[^<>&0-9])(?:[0-9]?>>?|&>>?)\s*([^\s;&|]+)")


def redirect_targets(text: str) -> list[str]:
    """Files written with > or >> (ignores &1-style fd duplications and /dev/null)."""
    out = []
    for m in _REDIRECT.finditer(text):
        t = m.group(1).strip("'\"")
        if t.startswith("&") or t in ("/dev/null", "nul", "NUL", "$null"):
            continue
        out.append(t)
    return out


def written_files(seg: Segment) -> list[str]:
    files = redirect_targets(seg.text)
    a = seg.argv
    if a and cmd_name(a[0]) == "tee":
        files.extend(w for w in a[1:] if not w.startswith("-"))
    if a and cmd_name(a[0]) in ("set-content", "add-content", "out-file", "sc", "ac"):
        for j, w in enumerate(a[1:], 1):
            if w.lower() in ("-path", "-filepath", "-literalpath") and j + 1 < len(a):
                files.append(a[j + 1])
        positional = [w for w in a[1:] if not w.startswith("-")]
        if positional:
            files.append(positional[0])
    return files


def is_heredoc(text: str) -> bool:
    return bool(re.search(r"<<-?\s*['\"]?\w+", text))


def text_of_input(inp: dict) -> str:
    """Flatten a tool input dict to text (for regex scanning of MCP / unknown tools)."""
    parts = []

    def walk(v):
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(inp)
    return "\n".join(parts)


def quote(s: str) -> str:
    return shlex.quote(s)
