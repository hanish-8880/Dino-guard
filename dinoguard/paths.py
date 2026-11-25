"""Filesystem locations. Every path can be redirected with env vars (used by tests)."""
from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    p = os.environ.get("DINOGUARD_HOME")
    base = Path(p) if p else Path.home() / ".dinoguard"
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return home() / "events.db"


def stream_path() -> Path:
    return home() / "stream.jsonl"


def config_path() -> Path:
    return home() / "config.json"


def secrets_path() -> Path:
    return home() / "secrets.json"


def socket_path() -> Path:
    return home() / "dinoguardd.sock"


def port_file() -> Path:
    """Used instead of a unix socket on platforms without AF_UNIX (Windows)."""
    return home() / "dinoguardd.port"


def pid_file() -> Path:
    return home() / "dinoguardd.pid"


def user_policies_dir() -> Path:
    return home() / "policies"


def packs_dir() -> Path:
    return home() / "packs"


def drafts_dir() -> Path:
    d = home() / "drafts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def backups_dir() -> Path:
    d = home() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def claude_home() -> Path:
    p = os.environ.get("DG_CLAUDE_HOME")
    return Path(p) if p else Path.home() / ".claude"


def claude_settings_path(scope: str = "user", cwd: str | None = None) -> Path:
    override = os.environ.get("DG_CLAUDE_SETTINGS")
    if override and scope == "user":
        return Path(override)
    if scope == "user":
        return claude_home() / "settings.json"
    root = Path(cwd or os.getcwd())
    if scope == "project":
        return root / ".claude" / "settings.json"
    if scope == "local":
        return root / ".claude" / "settings.local.json"
    raise ValueError(f"unknown scope {scope!r}")


def claude_projects_dir() -> Path:
    p = os.environ.get("DG_CLAUDE_PROJECTS")
    return Path(p) if p else claude_home() / "projects"
