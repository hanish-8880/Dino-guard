"""Where a pack comes from (CLAUDE.md §17.6): owner/repo[@tag], github:owner/repo[@tag], a release URL, or a
local folder. DG_NO_DOWNLOAD=1 forbids the network; DG_PACK_BASE_URL downloads from a mirror instead of GitHub."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .manifest import PackError

GITHUB = "https://github.com"
GITHUB_API = "https://api.github.com"

_REF = re.compile(r"([\w.-]+)/([\w.-]+)(?:@([\w.\-/]+))?")
_URL = re.compile(r"https?://[^/]+/([\w.-]+)/([\w.-]+)/releases(?:/(?:tag|download)/([^/?#]+))?(?:/[^?#]*)?/?")


@dataclass
class Source:
    kind: str                 # "github" | "dir"
    owner: str = ""
    repo: str = ""
    tag: str | None = None
    path: Path | None = None

    @property
    def label(self) -> str:
        if self.kind == "dir":
            return str(self.path)
        return f"{self.owner}/{self.repo}@{self.tag or 'latest'}"


def offline() -> bool:
    return os.environ.get("DG_NO_DOWNLOAD", "").strip().lower() in ("1", "true", "yes")


def mirror() -> str | None:
    base = os.environ.get("DG_PACK_BASE_URL", "").strip().rstrip("/")
    return base or None


def parse_source(ref: str) -> Source:
    ref = (ref or "").strip()
    if not ref:
        raise PackError("pack source required: owner/repo[@tag], github:owner/repo@tag, a release URL or a folder")
    p = Path(os.path.expanduser(ref))
    if p.is_dir():
        return Source("dir", path=p.resolve())
    if ref.startswith("github:"):
        ref = ref[len("github:"):]
    m = _URL.fullmatch(ref)
    if m:
        return Source("github", m.group(1), m.group(2), m.group(3))
    m = _REF.fullmatch(ref)
    if m:
        return Source("github", m.group(1), m.group(2), m.group(3))
    raise PackError(f"can't read pack source {ref!r}: use owner/repo[@tag], github:owner/repo@tag, "
                    "https://github.com/o/r/releases/tag/<tag>, or a local folder")


def need_network(src: Source) -> None:
    if offline():
        raise PackError(f"DG_NO_DOWNLOAD=1 is set, so {src.label} can't be downloaded. Install from a local "
                        "folder instead, or unset DG_NO_DOWNLOAD.")


def asset_urls(src: Source) -> tuple[str | None, dict[str, str]]:
    """Return (tag, {asset name: url}). With a mirror, assets are addressed directly (no API call)."""
    need_network(src)
    base = mirror()
    if base:
        root = (f"{base}/{src.owner}/{src.repo}/releases/download/{src.tag}" if src.tag
                else f"{base}/{src.owner}/{src.repo}/releases/latest/download")
        return src.tag, _MirrorAssets(root)
    import httpx
    url = f"{GITHUB_API}/repos/{src.owner}/{src.repo}/releases/" + (f"tags/{src.tag}" if src.tag else "latest")
    r = httpx.get(url, timeout=20, headers={"Accept": "application/vnd.github+json", "User-Agent": "dinoguard"})
    if r.status_code != 200:
        raise PackError(f"GitHub release not found ({r.status_code}) for {src.label}")
    rel = r.json()
    return rel.get("tag_name") or src.tag, {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}


class _MirrorAssets(dict):
    """Every asset name maps to <root>/<name>; a missing one fails at download time with a clear error."""

    def __init__(self, root: str):
        super().__init__()
        self.root = root

    def __contains__(self, name) -> bool:
        return True

    def __getitem__(self, name: str) -> str:
        return f"{self.root}/{name}"
