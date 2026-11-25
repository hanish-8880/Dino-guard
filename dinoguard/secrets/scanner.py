"""Secret detection + redaction: known token shapes plus high-entropy assignments."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY(?: BLOCK)?-----[\s\S]*?(?:-----END (?:[A-Z ]+ )?PRIVATE KEY(?: BLOCK)?-----|$)")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}")),
    ("stripe_key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("twilio_key", re.compile(r"\bSK[0-9a-fA-F]{32}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("url_credentials", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s:/@]{3,}@[^\s/]+", re.I)),
    ("password_assignment", re.compile(
        r"(?i)\b(?:password|passwd|pwd|db_pass(?:word)?)\b\s*[:=]\s*['\"]([^'\"\s]{6,})['\"]")),
]

_ASSIGN = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:api[_-]?key|secret|token|access[_-]?key|auth)[A-Z0-9_]*)\b\s*[:=]\s*['\"]?([A-Za-z0-9/+_\-.=]{20,})['\"]?")
_PLACEHOLDER = re.compile(r"(?i)(x{6,}|your[_-]|example|placeholder|changeme|dummy|<[^>]+>|\$\{|\$[A-Z_]|process\.env|os\.environ)")


@dataclass
class Finding:
    kind: str
    start: int
    end: int
    preview: str


def entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum((n / len(s)) * math.log2(n / len(s)) for n in counts.values())


def scan(text: str | None) -> list[Finding]:
    if not text:
        return []
    found: list[Finding] = []
    taken: list[tuple[int, int]] = []

    def add(kind, s, e):
        if any(s < te and e > ts for ts, te in taken):
            return
        val = text[s:e]
        if _PLACEHOLDER.search(val) and kind not in ("private_key", "aws_access_key"):
            return
        taken.append((s, e))
        found.append(Finding(kind, s, e, val[:4] + "…"))

    for kind, rx in PATTERNS:
        for m in rx.finditer(text):
            if kind == "password_assignment":
                add(kind, m.start(1), m.end(1))
            else:
                add(kind, m.start(), m.end())
    for m in _ASSIGN.finditer(text):
        val = m.group(2)
        if entropy(val) >= 3.5 and not _PLACEHOLDER.search(val):
            add("high_entropy_secret", m.start(2), m.end(2))
    return sorted(found, key=lambda f: f.start)


def redact(text: str | None) -> str | None:
    if not text:
        return text
    findings = scan(text)
    if not findings:
        return text
    out, last = [], 0
    for f in findings:
        out.append(text[last:f.start])
        out.append(f"[REDACTED:{f.kind}]")
        last = f.end
    out.append(text[last:])
    return "".join(out)


def redact_obj(obj):
    """Recursively redact strings inside JSON-like structures."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    return obj


def kinds(text: str | None) -> list[str]:
    return sorted({f.kind for f in scan(text)})
