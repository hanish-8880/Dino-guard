"""Undo hint (CLAUDE.md §17.12): blocking after a tool ran can't undo it, so before an *allowed* risky action in a
git repo we record a checkpoint. `git stash create` builds a commit of tracked changes without touching the working
tree, the index or the stash list. `dinoguard undo` prints it with the exact git commands; it never restores.

Limits (shown to the user): untracked files aren't in the checkpoint, and the commit is unreachable, so `git gc`
can prune it after the repo's prune window (about two weeks by default).
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess

from .store.db import rows
from .util import now_iso

RISKY_SHELL = re.compile(
    r"(?ix)(?:^|[;&|(\s])(?:"
    r"rm\s|rmdir\s|del\s|erase\s|remove-item\s|rd\s|"
    r"git\s+(?:checkout\s+(?:--\s|\.)|restore\s|reset\s+--hard|clean\s|stash\s+(?:drop|clear)|rebase\s|branch\s+-D)|"
    r"mv\s|move\s|move-item\s|"
    r"sed\s+-i|perl\s+-pi|truncate\s|find\s.*-delete"
    r")")
# `> file.py` truncates a source file (but not `>>` appends or `2>` stderr redirects)
REDIRECT = re.compile(r"(?<![>&\d])>\s*[\w./\\-]+\.(?:py|js|ts|tsx|go|rs|java|rb|json|ya?ml|toml|md|sql|sh|c|cpp|h)\b")
LARGE_WRITE_BYTES = 2048


def risky_reason(tool: str | None, inp: dict, cwd: str | None) -> str | None:
    if tool == "Bash":
        cmd = str(inp.get("command") or "")
        m = RISKY_SHELL.search(" " + cmd) or REDIRECT.search(cmd)
        return f"risky shell command ({m.group(0).strip()[:30]})" if m else None
    if tool == "Write":
        p = str(inp.get("file_path") or "")
        if p and cwd and not os.path.isabs(p):
            p = os.path.join(cwd, p)
        try:
            if p and os.path.isfile(p) and os.path.getsize(p) >= LARGE_WRITE_BYTES:
                return f"overwrite of an existing {os.path.getsize(p) // 1024} KB file"
        except OSError:
            return None
    return None


def _git(cwd: str, *args, timeout: float = 3.0) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def maybe_checkpoint(conn: sqlite3.Connection, event, decision, cfg: dict, git=_git) -> dict | None:
    """Record a checkpoint for an allowed risky PreToolUse in a git repo. Never raises."""
    try:
        if event.event != "PreToolUse" or decision.kind == "deny":
            return None
        if (cfg.get("undo") or {}).get("checkpoints", True) is False:
            return None
        cwd = event.session.get("cwd")
        reason = risky_reason(event.tool_name, event.tool_input, cwd)
        if not reason or not cwd or not os.path.isdir(cwd):
            return None
        rc, top = git(cwd, "rev-parse", "--show-toplevel")
        if rc != 0 or not top:
            return None
        _, head = git(top, "rev-parse", "HEAD")
        _, ref = git(top, "stash", "create", f"dinoguard checkpoint before {event.tool_name}")
        row = {"event_id": event.id, "session_id": event.session_id, "ts": now_iso(), "repo": top,
               "ref": ref or None, "head": head or None, "reason": reason}
        conn.execute("INSERT OR REPLACE INTO checkpoints(event_id, session_id, ts, repo, ref, head, reason) "
                     "VALUES (:event_id,:session_id,:ts,:repo,:ref,:head,:reason)", row)
        conn.commit()
        return row
    except Exception:  # noqa: BLE001 - a checkpoint must never break or slow a hook badly
        return None


def find(conn: sqlite3.Connection, event_id: str) -> dict | None:
    r = rows(conn, "SELECT c.*, e.tool_name, e.input_json FROM checkpoints c LEFT JOIN events e ON e.id=c.event_id "
                   "WHERE c.event_id=? OR c.event_id LIKE ? ORDER BY c.ts DESC LIMIT 1", (event_id, event_id + "%"))
    return r[0] if r else None


def recent(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    return rows(conn, "SELECT c.*, e.tool_name FROM checkpoints c LEFT JOIN events e ON e.id=c.event_id "
                      "ORDER BY c.ts DESC LIMIT ?", (limit,))


def instructions(cp: dict) -> list[str]:
    repo = cp["repo"]
    out = []
    if cp.get("ref"):
        out += [f"See what changed since the checkpoint:   git -C \"{repo}\" diff {cp['ref']}",
                f"Restore one file from it:                 git -C \"{repo}\" checkout {cp['ref']} -- <path>",
                f"Re-apply all checkpointed changes:        git -C \"{repo}\" stash apply {cp['ref']}"]
    elif cp.get("head"):
        out += ["The working tree had no uncommitted tracked changes at that moment, so the checkpoint is HEAD.",
                f"Restore one file:                          git -C \"{repo}\" checkout {cp['head']} -- <path>"]
    out.append("Untracked files are not in the checkpoint. Unreachable checkpoint commits can be pruned by git gc "
               "after about two weeks. Review before running anything; DinoGuard never restores on its own.")
    return out
