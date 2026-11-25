"""Read-only environment probe used by policies (git / gh / filesystem).

Policies get it as `ctx.probe` so tests can swap in a fake. Every call has a short timeout
and returns (returncode, stdout). Results are cached per probe instance (one evaluation).
"""
from __future__ import annotations

import os
import shutil
import subprocess


class Probe:
    def __init__(self, timeout: float = 3.0):
        self.timeout = timeout
        self._cache: dict[tuple, tuple[int, str]] = {}

    def _run(self, exe: str, cwd: str, args: tuple[str, ...], timeout: float | None = None) -> tuple[int, str]:
        key = (exe, cwd, args)
        if key in self._cache:
            return self._cache[key]
        if not shutil.which(exe):
            res = (127, "")
        else:
            try:
                p = subprocess.run([exe, *args], cwd=cwd or None, capture_output=True, text=True,
                                   timeout=timeout or self.timeout, stdin=subprocess.DEVNULL)
                res = (p.returncode, (p.stdout or "").strip())
            except (OSError, subprocess.TimeoutExpired):
                res = (124, "")
        self._cache[key] = res
        return res

    def git(self, cwd: str, *args: str) -> tuple[int, str]:
        return self._run("git", cwd, args)

    def gh(self, cwd: str, *args: str) -> tuple[int, str]:
        return self._run("gh", cwd, args, timeout=8.0)

    # convenience
    def branch(self, cwd: str) -> str | None:
        rc, out = self.git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
        return out if rc == 0 and out else None

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def listdir(self, path: str) -> list[str]:
        try:
            return os.listdir(path)
        except OSError:
            return []
