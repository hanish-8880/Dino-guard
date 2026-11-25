"""Policy declaration (@policy) and loading: built-ins, explicit files, project, user, packs, deployed."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import os
import pkgutil
import sys
import threading
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import paths
from .authority import normalize

MODULE_LOAD_DEADLINE_S = 10.0
SELF_PROTECT = "guard-self-protect"


@dataclass
class Policy:
    name: str
    fn: Callable
    description: str = ""
    events: list[str] | None = None        # None = all events
    category: str = "custom"
    authority: str = "hard"
    reviewed_by: list[str] = field(default_factory=list)
    default_enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)
    tools: list[str] | None = None         # optional tool filter (also used for narrow pack deny)
    source: str = "custom"                 # builtin | custom | pack:<id> | deployed
    default_mode: str = "observe"
    always_on: bool = False
    file: str | None = None
    version: int | None = None
    code_hash: str | None = None

    def matches(self, event: str, tool: str | None) -> bool:
        if self.events is not None and event not in self.events:
            return False
        if self.tools is not None and tool is not None and tool not in self.tools:
            return False
        if self.tools is not None and tool is None:
            return False
        return True

    def summary(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "events": self.events,
                "category": self.category, "authority": self.authority, "reviewed_by": self.reviewed_by,
                "default_enabled": self.default_enabled, "params": self.params, "source": self.source,
                "default_mode": self.default_mode, "always_on": self.always_on, "file": self.file,
                "version": self.version, "tools": self.tools}


def policy(name: str, description: str = "", events: list[str] | None = None, category: str = "custom",
           authority: str = "hard", reviewed_by: list[str] | None = None, default_enabled: bool = True,
           params: dict[str, Any] | None = None, tools: list[str] | None = None, **_ignored):
    """Declare a policy. Public SDK decorator."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("policy name must be a non-empty string")

    def deco(fn: Callable) -> Callable:
        auth, rb = normalize(authority, reviewed_by)
        fn.__dinoguard_policy__ = Policy(
            name=name.strip(), fn=fn, description=description,
            events=list(events) if events else None, category=category, authority=auth,
            reviewed_by=rb, default_enabled=default_enabled, params=dict(params or {}),
            tools=list(tools) if tools else None)
        return fn
    return deco


def builtin_policy(name: str, **kw):
    """Internal: built-ins are enforced by default when enabled."""
    always_on = kw.pop("always_on", False)
    default_mode = kw.pop("default_mode", "enforce")
    kw.setdefault("events", ["PreToolUse"])     # CLAUDE.md §6: built-ins default to PreToolUse
    inner = policy(name, **kw)

    def deco(fn):
        fn = inner(fn)
        p: Policy = fn.__dinoguard_policy__
        p.source, p.default_mode, p.always_on = "builtin", default_mode, always_on
        if always_on:
            p.authority, p.reviewed_by, p.default_enabled = "hard", [], True
        return fn
    return deco


def collect(namespace: dict | types.ModuleType) -> list[Policy]:
    ns = namespace.__dict__ if isinstance(namespace, types.ModuleType) else namespace
    out = []
    for obj in list(ns.values()):
        p = getattr(obj, "__dinoguard_policy__", None)
        if isinstance(p, Policy):
            out.append(p)
    return out


def _run_with_deadline(fn, deadline: float):
    box: dict[str, Any] = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - report every load failure
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(deadline)
    if t.is_alive():
        raise TimeoutError(f"took longer than {deadline:.0f}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def load_file(path: Path, deadline: float = MODULE_LOAD_DEADLINE_S) -> list[Policy]:
    mod_name = "dg_user_" + hashlib.sha1(str(path).encode()).hexdigest()[:12]

    def _load():
        spec = importlib.util.spec_from_file_location(mod_name, str(path))
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot import {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod

    mod = _run_with_deadline(_load, deadline)
    code_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    found = collect(mod)
    for p in found:
        p.file, p.code_hash = str(path), code_hash
    return found


def load_source(code: str, label: str, deadline: float = MODULE_LOAD_DEADLINE_S) -> list[Policy]:
    def _load():
        ns: dict[str, Any] = {"__name__": f"dg_src_{hashlib.sha1(label.encode()).hexdigest()[:10]}"}
        exec(compile(code, label, "exec"), ns)  # noqa: S102 - user-authored policy code
        return ns

    ns = _run_with_deadline(_load, deadline)
    found = collect(ns)
    h = hashlib.sha256(code.encode()).hexdigest()
    for p in found:
        p.file, p.code_hash = label, h
    return found


def discover_files(cwd: str | None, explicit: list[str] | tuple[str, ...] = ()) -> list[Path]:
    """Explicit files, then project, then user dirs (alphabetical). A file found twice loads once."""
    files: list[Path] = [Path(f) for f in explicit]
    dirs = []
    if cwd:
        dirs.append(Path(cwd) / ".dinoguard" / "policies")
    dirs.append(paths.user_policies_dir())
    for d in dirs:
        if d.is_dir():
            files.extend(sorted(p for p in d.glob("*policies.py") if p.is_file()))
    seen, out = set(), []
    for f in files:
        key = os.path.normcase(str(f.resolve()))
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


class Registry:
    def __init__(self):
        self.policies: list[Policy] = []
        self.errors: list[dict[str, str]] = []
        self.missing: list[dict[str, Any]] = []   # pack policies that failed to load (narrow deny)
        self._names: set[str] = set()

    def add(self, p: Policy, origin: str = "") -> bool:
        if p.name in self._names:
            self.errors.append({"file": origin or p.file or p.source,
                                "error": f"duplicate policy name {p.name!r} (skipped)"})
            return False
        self._names.add(p.name)
        self.policies.append(p)
        return True

    def get(self, name: str) -> Policy | None:
        return next((p for p in self.policies if p.name == name), None)

    def load_builtins(self) -> "Registry":
        from ..policies import builtin as pkg
        for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
            if info.name.startswith("_"):
                continue
            mod = importlib.import_module(f"{pkg.__name__}.{info.name}")
            for p in sorted(collect(mod), key=lambda p: p.name):
                self.add(p, f"builtin:{info.name}")
        # self-protection always evaluates first
        self.policies.sort(key=lambda p: (p.name != SELF_PROTECT, p.source != "builtin"))
        return self

    def load_custom(self, cwd: str | None = None, explicit=()) -> "Registry":
        for f in discover_files(cwd, explicit):
            try:
                found = load_file(f)
            except BaseException as e:  # noqa: BLE001
                self.errors.append({"file": str(f), "error": f"{type(e).__name__}: {e}"})
                continue
            for p in found:
                p.source = "custom"
                self.add(p, str(f))
        return self

    def load_packs(self) -> "Registry":
        try:
            from ..packs.loader import load_installed
        except ImportError:
            return self
        load_installed(self)
        return self

    def load_deployed(self) -> "Registry":
        try:
            from ..deploy.versions import load_deployed
        except ImportError:
            return self
        try:
            load_deployed(self)
        except Exception as e:  # noqa: BLE001
            self.errors.append({"file": "deployed", "error": f"{type(e).__name__}: {e}"})
        return self


def build(cwd: str | None = None, explicit=(), packs: bool = True, deployed: bool = True,
          custom: bool = True) -> Registry:
    r = Registry().load_builtins()
    if custom:
        r.load_custom(cwd, explicit)
    if deployed:
        r.load_deployed()
    if packs:
        r.load_packs()
    return r


def describe_params(fn: Callable) -> str:
    try:
        return str(inspect.signature(fn))
    except (TypeError, ValueError):
        return "(ctx)"
