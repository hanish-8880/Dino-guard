"""Minimal OpenAI-compatible chat client shared by reviewer, authoring, assistant and judge evals.

Configured via config["llm"]["url"] (or DG_LLM_URL) and key "llm_api_key" in secrets.json (or DG_LLM_KEY).
All callers must handle LLMUnavailable and degrade gracefully.
"""
from __future__ import annotations

import json
import os
import re

from . import config as config_mod


class LLMUnavailable(RuntimeError):
    pass


def settings(section: str = "llm") -> tuple[str | None, str | None, str]:
    cfg = config_mod.load()
    sec = cfg.get(section) or {}
    url = sec.get("url") or os.environ.get("DG_REVIEWER_URL" if section == "reviewer" else "DG_LLM_URL")
    if not url and section != "llm":
        url = (cfg.get("llm") or {}).get("url") or os.environ.get("DG_LLM_URL")
    key = config_mod.secret(f"{section}_api_key", "DG_REVIEWER_KEY" if section == "reviewer" else "DG_LLM_KEY") \
        or config_mod.secret("llm_api_key", "DG_LLM_KEY")
    model = sec.get("model") or (cfg.get("llm") or {}).get("model") or "gpt-4o-mini"
    return url, key, model


def complete(system: str, user: str, section: str = "llm", timeout: float = 20.0, max_tokens: int = 800) -> str:
    url, key, model = settings(section)
    if not url:
        raise LLMUnavailable("no LLM endpoint configured")
    import httpx
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    endpoint = url.rstrip("/") + ("" if url.rstrip("/").endswith("/chat/completions") else "/chat/completions")
    body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    effort = reasoning_effort(section, url)
    if effort:
        body["reasoning_effort"] = effort
    try:
        r = httpx.post(endpoint, headers=headers, timeout=timeout, json=body)
    except httpx.HTTPError as e:
        raise LLMUnavailable(f"LLM request failed: {e}") from e
    if r.status_code >= 300:
        raise LLMUnavailable(f"LLM HTTP {r.status_code}: {r.text[:200]}")
    try:
        choice = r.json()["choices"][0]
        text = choice["message"].get("content") or ""
    except (KeyError, IndexError, ValueError, AttributeError) as e:
        raise LLMUnavailable(f"unexpected LLM response: {e}") from e
    if choice.get("finish_reason") == "length":
        raise LLMUnavailable(f"the model's answer was cut off at max_tokens={max_tokens} (thinking models spend "
                             "part of it before answering); raise max_tokens or lower reasoning_effort")
    return text


def reasoning_effort(section: str, url: str | None) -> str | None:
    """`reasoning_effort` from config[section] (or [llm]); thinking models on Google's OpenAI-compatible endpoint
    default to "low" so the thinking budget doesn't crowd out the answer. Other endpoints get nothing unless set,
    because models without reasoning reject the field."""
    cfg = config_mod.load()
    val = (cfg.get(section) or {}).get("reasoning_effort") or (cfg.get("llm") or {}).get("reasoning_effort")
    if val:
        return None if str(val).lower() == "off" else str(val)
    if url and "generativelanguage.googleapis.com" in url:
        return "low"
    return None


def extract_json(text: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    body = m.group(1) if m else text
    start = min([i for i in (body.find("{"), body.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON in model output")
    return json.loads(body[start: max(body.rfind("}"), body.rfind("]")) + 1])


def complete_json(system: str, user: str, section: str = "llm", timeout: float = 20.0, max_tokens: int = 2048):
    text = complete(system, user, section, timeout, max_tokens=max_tokens)
    try:
        return extract_json(text)
    except ValueError as e:
        raise LLMUnavailable(f"model did not return JSON: {e}") from e
