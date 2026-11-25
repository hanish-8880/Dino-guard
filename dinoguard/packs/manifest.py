"""Pack manifest (dinoguard-pack.json) validation + SHA256SUMS handling.

SHA256SUMS proves the files match what the publisher listed (integrity). It is NOT a signature: it
does not prove who published them.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..version import __version__

MANIFEST = "dinoguard-pack.json"
SUMS = "SHA256SUMS"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class PackError(ValueError):
    pass


def _ver(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def validate(m: dict, builtin_names: set[str] | None = None) -> dict:
    for k in ("name", "publisher", "entry", "policies"):
        if k not in m:
            raise PackError(f"manifest missing {k!r}")
    if not _NAME.match(str(m["name"])) or not _NAME.match(str(m["publisher"])):
        raise PackError("pack name/publisher must be lowercase [a-z0-9_.-]")
    if "/" in m["entry"] or "\\" in m["entry"] or m["entry"].startswith("."):
        raise PackError("entry must be a plain file name in the pack root")
    if m.get("min_cli_version") and _ver(m["min_cli_version"]) > _ver(__version__):
        raise PackError(f"pack needs dinoguard >= {m['min_cli_version']} (this is {__version__})")
    seen = set()
    for p in m["policies"]:
        n = str(p.get("name", ""))
        if not n or "/" in n or not _NAME.match(n):
            raise PackError(f"bad policy name {n!r} (no '/', lowercase)")
        if n in seen:
            raise PackError(f"duplicate policy {n!r}")
        if builtin_names and n in builtin_names:
            raise PackError(f"pack policy {n!r} reuses a built-in name; packs cannot change built-ins")
        if p.get("always_on"):
            raise PackError("packs cannot declare always-on policies")
        seen.add(n)
    return m


def read_manifest(d: Path) -> dict:
    try:
        return json.loads((d / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PackError(f"cannot read {MANIFEST}: {e}") from e


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def parse_sums(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise PackError(f"malformed SHA256SUMS line: {line[:80]}")
        out[parts[1].lstrip("*")] = parts[0]
    return out


def write_sums(d: Path, files: list[str]) -> str:
    text = "".join(f"{sha256(d / f)}  {f}\n" for f in sorted(files))
    (d / SUMS).write_text(text, encoding="utf-8")
    return text


def verify_dir(d: Path, manifest: dict | None = None) -> str:
    """Check every listed file matches SHA256SUMS and the manifest + entry are covered. Returns the digest."""
    sums_p = d / SUMS
    if not sums_p.exists():
        raise PackError("SHA256SUMS missing")
    sums = parse_sums(sums_p.read_text(encoding="utf-8"))
    manifest = manifest or read_manifest(d)
    for required in (MANIFEST, manifest["entry"]):
        if required not in sums:
            raise PackError(f"{required} is not covered by SHA256SUMS")
    for f, h in sums.items():
        if "/" in f or "\\" in f or f.startswith("."):
            raise PackError(f"unsafe path in SHA256SUMS: {f}")
        fp = d / f
        if not fp.exists():
            raise PackError(f"{f} listed in SHA256SUMS but missing")
        if sha256(fp) != h:
            raise PackError(f"checksum mismatch for {f} (tampered or corrupted)")
    return hashlib.sha256(sums_p.read_bytes()).hexdigest()
