"""Immutable policy versions, deployments (observe/enforce) and rollback."""
from __future__ import annotations

import sqlite3

from .. import config
from ..engine.registry import load_source
from ..store.db import connect, one, rows
from ..util import new_id, now_iso


def _conn(conn):
    return conn or connect()


def _source_for(name: str) -> str:
    from ..authoring.draft import load_draft
    _, p = load_draft(name)
    return p.read_text(encoding="utf-8")


def publish(name: str, notes: str = "", code: str | None = None, conn: sqlite3.Connection | None = None) -> int:
    c = _conn(conn)
    code = code if code is not None else _source_for(name)
    found = load_source(code, f"publish:{name}")
    if name not in [p.name for p in found]:
        raise ValueError(f"code does not define policy {name!r}")
    from ..engine.registry import Registry
    if Registry().load_builtins().get(name):
        raise ValueError(f"{name!r} clashes with a built-in policy name")
    last = one(c, "SELECT MAX(version) v FROM policy_versions WHERE name=?", (name,))
    if last and last["v"]:
        prev = one(c, "SELECT code FROM policy_versions WHERE name=? AND version=?", (name, last["v"]))
        if prev and prev["code"] == code:
            return last["v"]          # identical code: no new version
    v = (last["v"] or 0) + 1 if last else 1
    c.execute("INSERT INTO policy_versions(name, version, code, created_at, notes) VALUES (?,?,?,?,?)",
              (name, v, code, now_iso(), notes))
    c.commit()
    return v


def deploy(name: str, version: int | None = None, mode: str = "observe", conn=None, rolled_back_from=None) -> int:
    if mode not in ("observe", "enforce"):
        raise ValueError("mode must be observe|enforce")
    c = _conn(conn)
    if version is None:
        r = one(c, "SELECT MAX(version) v FROM policy_versions WHERE name=?", (name,))
        version = r["v"] if r else None
    if not version or not one(c, "SELECT 1 x FROM policy_versions WHERE name=? AND version=?", (name, version)):
        raise ValueError(f"{name} v{version} is not published (dinoguard policy publish {name})")
    c.execute("INSERT OR REPLACE INTO policies(name, source, version, enabled, mode, authority, params_json, code_hash) "
              "VALUES (?,?,?,?,?,?,?,?)", (name, "deployed", version, 1, mode, "hard", "{}", None))
    c.execute("INSERT INTO deployments(id, policy, version, mode, scope, created_at, rolled_back_from) VALUES (?,?,?,?,?,?,?)",
              (new_id("dep"), name, version, mode, "user", now_iso(), rolled_back_from))
    c.commit()
    config.set_policy(name, enabled=True, mode=mode)
    return version


def rollback(name: str, conn=None) -> int:
    c = _conn(conn)
    deps = rows(c, "SELECT * FROM deployments WHERE policy=? ORDER BY created_at DESC, rowid DESC", (name,))
    if not deps:
        raise ValueError(f"{name} was never deployed")
    current = deps[0]
    prev = next((d for d in deps[1:] if d["version"] != current["version"] and not d["rolled_back_from"]), None)
    if prev is None:
        raise ValueError(f"no earlier good version of {name} to roll back to")
    return deploy(name, prev["version"], prev["mode"], c, rolled_back_from=current["version"])


def list_versions(name: str, conn=None) -> list[dict]:
    return rows(_conn(conn), "SELECT version, created_at, notes FROM policy_versions WHERE name=? ORDER BY version",
                (name,))


def load_deployed(registry, conn=None) -> None:
    c = _conn(conn)
    for r in rows(c, "SELECT p.name, p.version, v.code FROM policies p JOIN policy_versions v ON v.name=p.name "
                     "AND v.version=p.version WHERE p.source='deployed' AND p.enabled=1"):
        try:
            found = load_source(r["code"], f"deployed:{r['name']}@v{r['version']}")
        except BaseException as e:  # noqa: BLE001
            registry.errors.append({"file": f"deployed:{r['name']}", "error": f"{type(e).__name__}: {e}"})
            continue
        for p in found:
            if p.name != r["name"]:
                continue
            p.source, p.version = "deployed", r["version"]
            registry.add(p, f"deployed:{p.name}")
