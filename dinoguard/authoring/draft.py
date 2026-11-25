"""Plain English (or an audit finding) -> draft Python policy + 2 should-block / 2 should-allow examples.

Uses the configured LLM when available; otherwise a template picker (path / command / regex).
Drafts live in ~/.dinoguard/drafts/<name>.py and are NOT loaded until published + deployed.
"""
from __future__ import annotations

import json
import re

from .. import paths
from ..engine.registry import load_source
from ..llm import LLMUnavailable, complete
from .templates import command_template, path_template, regex_template


def slug(text: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in ("the", "a", "an", "any", "to", "of")]
    return ("custom-" + "-".join(words[:5]))[:60] or "custom-policy"


def pick_template(text: str, name: str) -> tuple[str, dict, str]:
    t = text.strip()
    q = re.search(r"[`'\"]([^`'\"]+)[`'\"]", t)
    m_path = re.search(r"(?i)\b(?:touch|edit|modify|change|write to|read|open)\b.*?\b(?:the\s+)?([\w./-]+)\s+"
                       r"(?:folder|directory|dir|files?|path)\b", t) or \
        re.search(r"(?i)\b(?:folder|directory|dir|path|file)\s+[`'\"]?([\w./-]+)", t)
    m_cmd = re.search(r"(?i)\b(?:run|use|call|execute|invoke)\s+[`'\"]?([\w./:-]+(?:\s+[\w./:-]+)?)", t)
    if m_path and not re.search(r"(?i)\b(?:run|execute)\b", t):
        target = m_path.group(1).strip("/")
        return path_template(name, text, target), {
            "block": [{"tool": "Write", "input": {"file_path": f"src/{target}/a.py", "content": "x"}},
                      {"tool": "Bash", "input": {"command": f"rm {target}/config.json"}}],
            "allow": [{"tool": "Write", "input": {"file_path": "src/other/a.py", "content": "x"}},
                      {"tool": "Bash", "input": {"command": "ls"}}]}, "template:path"
    if m_cmd:
        cmd = (q.group(1) if q else m_cmd.group(1)).strip()
        return command_template(name, text, cmd), {
            "block": [{"tool": "Bash", "input": {"command": cmd}}, {"tool": "Bash", "input": {"command": f"cd x && {cmd} --yes"}}],
            "allow": [{"tool": "Bash", "input": {"command": "ls -la"}}, {"tool": "Bash", "input": {"command": "git status"}}]
        }, "template:command"
    pattern = q.group(1) if q else (re.findall(r"[\w./-]{4,}", t) or ["TODO"])[-1]
    return regex_template(name, text, re.escape(pattern)), {
        "block": [{"tool": "Bash", "input": {"command": f"echo {pattern}"}},
                  {"tool": "Write", "input": {"file_path": "a.txt", "content": f"contains {pattern}"}}],
        "allow": [{"tool": "Bash", "input": {"command": "ls"}}, {"tool": "Write", "input": {"file_path": "b.txt", "content": "ok"}}]
    }, "template:regex"


LLM_SYSTEM = """You write DinoGuard policies in Python. Output ONLY a JSON object with keys:
"code": a complete Python module that does `from dinoguard import policy, allow, deny, instruct` and defines ONE
function decorated with @policy(name=NAME, description=..., events=["PreToolUse"], category="custom").
The function gets ctx with ctx.tool_name ("Bash","Read","Write","Edit",...), ctx.tool_input (Bash: command;
Write: file_path, content; Edit: file_path, old_string, new_string), ctx.cwd. Return allow(), or deny(reason)
where reason tells the agent what was detected and what to do instead. No imports besides re/os/dinoguard.
"examples": {"block": [2 objects {"tool","input"}], "allow": [2 objects {"tool","input"}]}."""


def draft_policy(text: str, name: str | None = None) -> dict:
    name = name or slug(text)
    method = None
    try:
        raw = complete(LLM_SYSTEM.replace("NAME", json.dumps(name)), text, timeout=40)
        from ..llm import extract_json
        obj = extract_json(raw)
        code, examples = obj["code"], obj["examples"]
        found = load_source(code, f"draft:{name}")
        if [p.name for p in found] != [name]:
            raise ValueError("LLM draft did not define exactly one policy with the requested name")
        method = "llm"
    except (LLMUnavailable, ValueError, KeyError, TypeError, SyntaxError):
        code, examples, method = pick_template(text, name)
    p = paths.drafts_dir() / f"{name}.py"
    p.write_text(code, encoding="utf-8")
    (paths.drafts_dir() / f"{name}.examples.json").write_text(json.dumps(examples, indent=2), encoding="utf-8")
    return {"name": name, "path": str(p), "code": code, "examples": examples, "method": method}


def load_draft(name: str):
    p = paths.drafts_dir() / f"{name}.py"
    if not p.exists():
        raise FileNotFoundError(f"no draft named {name!r} in {paths.drafts_dir()}")
    found = load_source(p.read_text(encoding="utf-8"), str(p))
    pol = next((x for x in found if x.name == name), None)
    if pol is None:
        raise ValueError(f"{p} does not define a policy named {name!r}")
    return pol, p


def test_policy(name: str) -> dict:
    from ..engine.context import PolicyContext
    from ..engine.decisions import allow
    from ..engine.events import CanonicalEvent, make_tool
    pol, p = load_draft(name)
    ex_path = paths.drafts_dir() / f"{name}.examples.json"
    examples = json.loads(ex_path.read_text(encoding="utf-8")) if ex_path.exists() else {"block": [], "allow": []}
    results = []
    for expect, items in (("block", examples.get("block", [])), ("allow", examples.get("allow", []))):
        for ex in items:
            ev = CanonicalEvent(event=ex.get("event", "PreToolUse"), session={"id": "test", "cwd": "."},
                                tool=make_tool(ex["tool"], ex.get("input")))
            try:
                d = pol.fn(PolicyContext(event=ev, params=dict(pol.params))) or allow()
                got = d.kind
            except Exception as e:  # noqa: BLE001
                got = f"error:{type(e).__name__}"
            ok = (got in ("deny", "instruct")) if expect == "block" else got == "allow"
            results.append({"expect": expect, "got": got, "ok": ok, "example": json.dumps(ex)[:120]})
    return {"name": name, "passed": bool(results) and all(r["ok"] for r in results), "results": results}


def draft_from_finding(conn, finding_id: str) -> dict:
    r = conn.execute("SELECT failure_mode, evidence_json FROM findings WHERE id=?", (finding_id,)).fetchone()
    if not r:
        raise KeyError(finding_id)
    ev = json.loads(r["evidence_json"] or "[]")
    sample = (ev[0].get("summary") if ev else "") or ""
    words = sample.split()[:2]
    if words and (ev[0].get("tool") == "Bash"):
        text = f"never run `{' '.join(words)}` ({r['failure_mode']})"
    else:
        text = f"block '{sample[:40] or r['failure_mode']}'"
    return draft_policy(text)
