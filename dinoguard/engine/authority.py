"""hard / reviewable authority. Anything malformed falls back to hard (fail closed)."""
from __future__ import annotations

HARD, REVIEWABLE = "hard", "reviewable"


def normalize(authority, reviewed_by=None) -> tuple[str, list[str]]:
    """Return (authority, reviewed_by). Reviewable requires a non-empty list of check names."""
    if authority == REVIEWABLE:
        if isinstance(reviewed_by, (list, tuple)) and reviewed_by and all(
            isinstance(c, str) and c.strip() for c in reviewed_by
        ):
            return REVIEWABLE, [c.strip() for c in reviewed_by]
        return HARD, []
    return HARD, []
