"""Lexicon-based frustration score for user prompts (0 calm .. 1 very frustrated). No model needed."""
from __future__ import annotations

import re

_STRONG = ["wtf", "what the hell", "are you kidding", "useless", "stupid", "idiot", "terrible", "garbage",
           "for the last time", "i told you", "stop doing", "you broke", "you deleted", "ffs", "damn", "seriously?"]
_MILD = ["again", "still not", "still broken", "doesn't work", "does not work", "not working", "wrong", "no,",
         "nope", "why did you", "i said", "please just", "undo", "revert", "that's not", "thats not", "ugh", "annoying"]


def prompt_score(text: str) -> float:
    t = (text or "").lower()
    if not t.strip():
        return 0.0
    s = 0.0
    s += 0.45 * sum(1 for w in _STRONG if w in t)
    s += 0.2 * sum(1 for w in _MILD if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", t))
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        s += 0.3
    s += 0.15 * min(3, text.count("!!") + text.count("??"))
    return min(1.0, s)


def frustration(prompts) -> float:
    """Session-level frustration: max of recent prompt scores, weighted toward the latest prompts."""
    if isinstance(prompts, str):
        prompts = [prompts]
    scores = [prompt_score(p) for p in prompts or []]
    if not scores:
        return 0.0
    recent = scores[-5:]
    return round(min(1.0, 0.7 * max(recent) + 0.3 * (sum(recent) / len(recent))), 3)
