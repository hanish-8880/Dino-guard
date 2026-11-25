"""Load installed packs into the registry. Re-verify the pinned digest before every load.

Tampered / retagged pack -> refuse to load. Enforced pack that loads fewer policies than selected ->
those policies go to registry.missing, which the engine turns into a narrow deny (pack/pack-unavailable).
"""
from __future__ import annotations

import json

from .. import paths
from ..engine.authority import normalize
from ..engine.registry import load_file
from ..store.db import connect, rows
from .install import pack_dir
from .manifest import PackError, verify_dir


def _missing(reg, m: dict, sel: dict, names: list[str], error: str, transient: bool = False) -> None:
    by_name = {p["name"]: p for p in m.get("policies", [])}
    for n in names:
        decl = by_name.get(n, {})
        reg.missing.append({"pack": f"{m['publisher']}/{m['name']}", "name": f"{m['name']}/{n}",
                            "events": decl.get("events"), "tools": decl.get("tools"),
                            "mode": sel.get("mode", "observe"), "error": error, "transient": transient})


def load_pack(reg, row: dict) -> None:
    m = json.loads(row["manifest_json"])
    sel = json.loads(row["selection_json"] or "{}")
    selected = list(sel.get("policies") or [])
    d = pack_dir(row["publisher"], row["name"], row["tag"])
    label = f"pack:{row['publisher']}/{row['name']}@{row['tag']}"
    try:
        digest = verify_dir(d, m)
        if digest != row["digest"]:
            raise PackError("digest differs from the one pinned at install (retagged or modified) - refusing to load")
    except PackError as e:
        reg.errors.append({"file": label, "error": str(e)})
        _missing(reg, m, sel, selected, str(e))
        return
    try:
        found = load_file(d / m["entry"])
    except TimeoutError as e:
        reg.errors.append({"file": label, "error": f"load timeout: {e}"})
        _missing(reg, m, sel, selected, "load timeout", transient=True)
        return
    except BaseException as e:  # noqa: BLE001
        reg.errors.append({"file": label, "error": f"{type(e).__name__}: {e}"})
        _missing(reg, m, sel, selected, f"{type(e).__name__}: {e}")
        return
    decl = {p["name"]: p for p in m["policies"]}
    loaded = set()
    for p in found:
        if p.name not in decl or p.name not in selected or "/" in p.name:
            continue
        dp = decl[p.name]
        p.name = f"{m['name']}/{p.name}"
        p.source, p.default_mode, p.always_on = label, sel.get("mode", "observe"), False
        p.authority, p.reviewed_by = normalize(dp.get("authority", "hard"), dp.get("reviewed_by"))
        p.default_enabled = bool(dp.get("default_enabled", True))
        p.category = dp.get("category", p.category)
        if reg.add(p, label):
            loaded.add(dp["name"])
    lost = [n for n in selected if n not in loaded]
    if lost:
        reg.errors.append({"file": label, "error": f"manifest declares {len(selected)} selected policies, "
                                                   f"loaded {len(loaded)}: missing {lost}"})
        _missing(reg, m, sel, lost, "declared in manifest but not loaded")


def load_installed(reg) -> None:
    if not paths.db_path().exists():
        return
    try:
        for row in rows(connect(), "SELECT * FROM packs ORDER BY publisher, name"):
            load_pack(reg, row)
    except Exception as e:  # noqa: BLE001
        reg.errors.append({"file": "packs", "error": f"{type(e).__name__}: {e}"})


def verify_all() -> list[tuple[str, bool, str]]:
    out = []
    if not paths.db_path().exists():
        return out
    for row in rows(connect(), "SELECT * FROM packs"):
        name = f"{row['publisher']}/{row['name']}@{row['tag']}"
        try:
            dg = verify_dir(pack_dir(row["publisher"], row["name"], row["tag"]), json.loads(row["manifest_json"]))
            ok = dg == row["digest"]
            out.append((name, ok, "digest matches (integrity only, not a signature)" if ok else "DIGEST CHANGED"))
        except PackError as e:
            out.append((name, False, str(e)))
    return out
