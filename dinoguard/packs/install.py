"""Install / show / remove packs. A pack = GitHub release with dinoguard-pack.json, entry file, SHA256SUMS."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from .. import paths
from ..store.db import connect, rows
from ..util import now_iso
from .manifest import MANIFEST, SUMS, PackError, read_manifest, validate, verify_dir

GITHUB_API = "https://api.github.com"


def parse_ref(ref: str) -> tuple[str, str, str | None]:
    m = re.fullmatch(r"([\w.-]+)/([\w.-]+)(?:@([\w.\-/]+))?", ref or "")
    if not m:
        raise PackError("pack reference must look like owner/repo[@tag]")
    return m.group(1), m.group(2), m.group(3)


def _builtin_names() -> set[str]:
    from ..engine.registry import Registry
    return {p.name for p in Registry().load_builtins().policies}


def _release(owner: str, repo: str, tag: str | None) -> dict:
    import httpx
    url = f"{GITHUB_API}/repos/{owner}/{repo}/releases/" + (f"tags/{tag}" if tag else "latest")
    r = httpx.get(url, timeout=20, headers={"Accept": "application/vnd.github+json", "User-Agent": "dinoguard"})
    if r.status_code != 200:
        raise PackError(f"GitHub release not found ({r.status_code}) for {owner}/{repo}@{tag or 'latest'}")
    return r.json()


def _download(asset_url: str, dest: Path) -> None:
    import httpx
    with httpx.stream("GET", asset_url, timeout=60, follow_redirects=True,
                      headers={"Accept": "application/octet-stream", "User-Agent": "dinoguard"}) as r:
        if r.status_code != 200:
            raise PackError(f"download failed ({r.status_code}): {asset_url}")
        size = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > 5 * 1024 * 1024:
                    raise PackError("pack asset larger than 5 MB")
                f.write(chunk)


def pack_dir(publisher: str, name: str, tag: str) -> Path:
    return paths.packs_dir() / publisher / name / tag


def install_from_dir(src: Path, tag: str, policies=None, categories=None, select_all: bool = False,
                     mode: str = "observe", conn=None) -> dict:
    """Verify a downloaded pack folder, copy it into ~/.dinoguard/packs and record digest + selection."""
    m = validate(read_manifest(src), _builtin_names())
    verify_dir(src, m)
    names = [p["name"] for p in m["policies"]]
    if select_all or not (policies or categories):
        chosen = names
    else:
        chosen = [p["name"] for p in m["policies"] if p["name"] in (policies or []) or p.get("category") in (categories or [])]
    unknown = set(policies or []) - set(names)
    if unknown:
        raise PackError(f"pack has no policies named {sorted(unknown)}")
    dest = pack_dir(m["publisher"], m["name"], tag)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    from .manifest import parse_sums
    for f in parse_sums((src / SUMS).read_text(encoding="utf-8")):
        shutil.copyfile(src / f, dest / f)
    shutil.copyfile(src / SUMS, dest / SUMS)
    digest = verify_dir(dest, m)
    c = conn or connect()
    c.execute("INSERT OR REPLACE INTO packs(publisher, name, tag, digest, manifest_json, selection_json, installed_at) "
              "VALUES (?,?,?,?,?,?,?)", (m["publisher"], m["name"], tag, digest, json.dumps(m),
                                         json.dumps({"policies": chosen, "mode": mode}), now_iso()))
    c.commit()
    return {"publisher": m["publisher"], "name": m["name"], "tag": tag, "digest": digest, "policies": chosen}


def install_pack(ref: str, policies=None, categories=None, select_all=False, mode="observe") -> dict:
    owner, repo, tag = parse_ref(ref)
    rel = _release(owner, repo, tag)
    tag = rel.get("tag_name") or tag
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    if MANIFEST not in assets or SUMS not in assets:
        raise PackError(f"release {tag} lacks {MANIFEST} or {SUMS}")
    with tempfile.TemporaryDirectory(prefix="dg-pack-") as tmp:
        t = Path(tmp)
        _download(assets[MANIFEST], t / MANIFEST)
        _download(assets[SUMS], t / SUMS)
        m = read_manifest(t)
        if m["entry"] not in assets:
            raise PackError(f"release lacks entry file {m['entry']}")
        _download(assets[m["entry"]], t / m["entry"])
        return install_from_dir(t, tag, policies, categories, select_all, mode)


def show_manifest(ref: str) -> dict:
    """Read only the manifest (never runs pack code)."""
    owner, repo, tag = parse_ref(ref)
    for r in rows(connect(), "SELECT manifest_json, tag FROM packs WHERE publisher=? AND name=?", (owner, repo)):
        return {"installed_tag": r["tag"], **json.loads(r["manifest_json"])}
    rel = _release(owner, repo, tag)
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    if MANIFEST not in assets:
        raise PackError(f"release lacks {MANIFEST}")
    with tempfile.TemporaryDirectory(prefix="dg-pack-") as tmp:
        _download(assets[MANIFEST], Path(tmp) / MANIFEST)
        return {"tag": rel.get("tag_name"), **read_manifest(Path(tmp))}


def remove_pack(ref: str, conn=None) -> None:
    publisher, name, _ = parse_ref(ref)
    c = conn or connect()
    c.execute("DELETE FROM packs WHERE publisher=? AND name=?", (publisher, name))
    c.commit()
    d = paths.packs_dir() / publisher / name
    if d.exists():
        shutil.rmtree(d)
