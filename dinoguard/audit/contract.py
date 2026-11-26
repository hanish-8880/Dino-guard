"""Agent contract: what the agent should and should not do (CLAUDE.md §17.4).

The project's `.dinoguard/contract.yaml` wins over `contract.yaml` in the DinoGuard home. `should_not` rules with
`paths`, `commands` (regex) or `tools` are checked deterministically; anything else needs human review.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .. import paths

FILE = "contract.yaml"


def contract_path(cwd: str | None) -> Path | None:
    if cwd:
        p = Path(cwd) / ".dinoguard" / FILE
        if p.is_file():
            return p
    p = paths.home() / FILE
    return p if p.is_file() else None


def parse(data: Any) -> dict:
    """Normalise a loaded YAML document into {"should": [...], "should_not": [rule...], "errors": [...]}."""
    data = data if isinstance(data, dict) else {}
    out = {"should": [str(s) for s in (data.get("should") or []) if s], "should_not": [], "errors": []}
    for i, item in enumerate(data.get("should_not") or []):
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict) or not item.get("text"):
            out["errors"].append(f"should_not[{i}] needs a text")
            continue
        rule = {"text": str(item["text"]), "paths": [str(p).replace("\\", "/") for p in item.get("paths") or []],
                "tools": [str(t) for t in item.get("tools") or []], "commands": []}
        for rx in item.get("commands") or []:
            try:
                rule["commands"].append(re.compile(str(rx), re.I))
            except re.error as e:
                out["errors"].append(f"should_not[{i}] bad regex {rx!r}: {e}")
        rule["machine"] = bool(rule["paths"] or rule["tools"] or rule["commands"])
        out["should_not"].append(rule)
    return out


_cache: dict[str, tuple[float, dict]] = {}


def load(cwd: str | None) -> dict | None:
    p = contract_path(cwd)
    if p is None:
        return None
    try:
        mtime = p.stat().st_mtime
        hit = _cache.get(str(p))
        if hit and hit[0] == mtime:
            return hit[1]
        import yaml
        c = parse(yaml.safe_load(p.read_text(encoding="utf-8")))
        c["path"] = str(p)
        _cache[str(p)] = (mtime, c)
        return c
    except Exception as e:  # noqa: BLE001 - a broken contract is reported, never fatal
        return {"should": [], "should_not": [], "errors": [f"{p}: {e}"], "path": str(p)}


def _targets(tool: str | None, inp: dict) -> list[str]:
    vals = [inp.get(k) for k in ("file_path", "path", "notebook_path")]
    if tool == "Bash":
        vals.append(inp.get("command"))
    return [str(v).replace("\\", "/") for v in vals if v]


def _path_hit(fragment: str, targets: list[str]) -> bool:
    frag = fragment.strip("/").lower()
    if not frag:
        return False
    for t in targets:
        tl = "/" + t.lower()
        for token in re.split(r"[\s'\"=]+", tl):
            token = "/" + token.lstrip("./")
            if f"/{frag}/" in token + "/":
                return True
    return False


def violations(contract: dict | None, tool: str | None, inp: dict) -> list[dict]:
    """Machine-checkable should_not rules this tool call breaks."""
    if not contract or not tool:
        return []
    inp = inp or {}
    targets = _targets(tool, inp)
    cmd = str(inp.get("command") or "") if tool == "Bash" else ""
    out = []
    for r in contract.get("should_not") or []:
        if not r.get("machine"):
            continue
        if (r["tools"] and any(tool == t or tool.startswith(t + ":") for t in r["tools"])) \
                or (r["paths"] and any(_path_hit(p, targets) for p in r["paths"])) \
                or (cmd and any(rx.search(cmd) for rx in r["commands"])):
            out.append(r)
    return out


def review_items(contract: dict | None) -> list[str]:
    """Contract lines a machine can't check: listed in audits as 'needs human review'."""
    if not contract:
        return []
    return list(contract.get("should") or []) + [r["text"] for r in contract.get("should_not") or []
                                                 if not r.get("machine")]
