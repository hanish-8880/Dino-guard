"""`dinoguard publish`: build a pack folder from local custom policy files."""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..engine.registry import build, discover_files
from ..version import __version__
from .manifest import MANIFEST, write_sums


def build_pack(name: str | None, publisher: str, out: str) -> dict:
    cwd = os.getcwd()
    files = discover_files(cwd)
    if not files:
        raise SystemExit("no *policies.py files found in .dinoguard/policies or ~/.dinoguard/policies")
    reg = build(cwd, packs=False, deployed=False)
    custom = [p for p in reg.policies if p.source == "custom"]
    name = (name or Path(cwd).name).lower().replace(" ", "-")
    d = Path(out)
    d.mkdir(parents=True, exist_ok=True)
    entry = "policies.py"
    parts = [f"# Built by dinoguard {__version__} from: {', '.join(f.name for f in files)}\n"]
    for f in files:
        parts.append(f"\n# ---- {f.name} ----\n" + f.read_text(encoding="utf-8"))
    (d / entry).write_text("".join(parts), encoding="utf-8")
    manifest = {"publisher": publisher, "name": name, "version": "0.1.0", "entry": entry,
                "min_cli_version": __version__, "categories": sorted({p.category for p in custom}),
                "defaults": {"mode": "observe"},
                "policies": [{"name": p.name, "description": p.description, "category": p.category,
                              "events": p.events, "tools": p.tools, "authority": p.authority,
                              "reviewed_by": p.reviewed_by, "default_enabled": p.default_enabled} for p in custom]}
    (d / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_sums(d, [MANIFEST, entry])
    return {"name": name, "out": str(d), "policies": [p.name for p in custom]}
