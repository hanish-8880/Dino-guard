"""Shared tool-name / input normalisation for non-Claude harnesses."""
from __future__ import annotations

import json
from typing import Any

# native tool name (lowercased) -> canonical
ALIASES = {
    "shell": "Bash", "bash": "Bash", "run_shell_command": "Bash", "powershell": "Bash", "terminal": "Bash",
    "run_terminal_cmd": "Bash", "exec": "Bash", "execute": "Bash", "command": "Bash",
    "read": "Read", "read_file": "Read", "view": "Read", "read_many_files": "Read", "cat": "Read",
    "write": "Write", "write_file": "Write", "create": "Write", "create_file": "Write",
    "edit": "Edit", "replace": "Edit", "str_replace": "Edit", "edit_file": "Edit", "apply_patch": "Edit",
    "patch": "Edit", "multiedit": "Edit",
    "grep": "Grep", "search_file_content": "Grep", "grep_search": "Grep", "rg": "Grep",
    "glob": "Glob", "list_directory": "Glob", "ls": "Glob", "find": "Glob",
    "web_fetch": "WebFetch", "webfetch": "WebFetch", "fetch": "WebFetch",
    "google_web_search": "WebSearch", "web_search": "WebSearch", "websearch": "WebSearch",
    "task": "Task", "agent": "Task", "subagent": "Task",
    "fetchurl": "WebFetch", "read_url_content": "WebFetch", "search_web": "WebSearch", "web_extract": "WebFetch",
    "notebook_read": "Read", "notebook_edit": "Edit", "search_files": "Grep",
    "run_subagent": "Task", "invoke_subagent": "Task", "delegate_task": "Task",
}
_PATH_KEYS = ("file_path", "path", "absolute_path", "filePath", "filename", "file", "target_file")
_CMD_KEYS = ("command", "cmd", "script", "commandLine")


def patch_path(patch: str) -> str:
    """First file named in an apply_patch / unified-diff text ("" if none)."""
    for line in (patch or "").splitlines():
        for marker in ("*** Update File: ", "*** Add File: ", "*** Delete File: ", "+++ b/"):
            if line.startswith(marker):
                return line[len(marker):].strip()
    return ""


def parse_args(v) -> dict[str, Any]:
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            o = json.loads(v)
            return o if isinstance(o, dict) else {"value": o}
        except ValueError:
            return {"value": v}
    return {} if v is None else {"value": v}


def canonical_input(tool: str, inp: dict[str, Any]) -> dict[str, Any]:
    out = dict(inp)
    if tool == "Bash":
        cmd = next((inp[k] for k in _CMD_KEYS if inp.get(k)), "")
        if isinstance(cmd, list):
            cmd = " ".join(map(str, cmd))
        out["command"] = str(cmd)
    elif tool in ("Read", "Write", "Edit"):
        p = next((inp[k] for k in _PATH_KEYS if inp.get(k)), "")
        out["file_path"] = str(p)
        if tool == "Write":
            out["content"] = str(inp.get("content") or inp.get("file_text") or inp.get("text") or "")
        if tool == "Edit":
            out["old_string"] = str(inp.get("old_string") or inp.get("old_str") or inp.get("oldString") or "")
            out["new_string"] = str(inp.get("new_string") or inp.get("new_str") or inp.get("newString")
                                    or inp.get("patch") or inp.get("patchText") or inp.get("input") or "")
            if not out["file_path"]:
                out["file_path"] = patch_path(out["new_string"])
    elif tool in ("Grep", "Glob"):
        out["pattern"] = str(inp.get("pattern") or inp.get("query") or inp.get("glob") or "")
        if any(inp.get(k) for k in _PATH_KEYS + ("dir_path", "directory")):
            out["path"] = str(next(inp[k] for k in _PATH_KEYS + ("dir_path", "directory") if inp.get(k)))
    return out


def map_generic(name: str | None, inp, mcp_prefix: str | None = None) -> tuple[str, dict]:
    args = parse_args(inp)
    if not name:
        return "Other", args
    n = str(name)
    if n.startswith("MCP:"):
        return n, args
    if mcp_prefix and n.startswith(mcp_prefix):
        rest = n[len(mcp_prefix):]
        server, _, tool = rest.partition("_")
        return f"MCP:{server}:{tool or '?'}", args
    if n.startswith("mcp__"):
        parts = n.split("__")
        return f"MCP:{parts[1] if len(parts) > 1 else '?'}:{'__'.join(parts[2:]) or '?'}", args
    canon = ALIASES.get(n.lower()) or (n if n in ("Bash", "Read", "Write", "Edit", "Grep", "Glob", "WebFetch",
                                                 "WebSearch", "Task") else None)
    if not canon:
        return "Other", {"_tool": n, **args}
    return canon, canonical_input(canon, args)
