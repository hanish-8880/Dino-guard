"""Goose adapter (stub). Goose documents no pre-tool hook mechanism (only MCP extensions, ACP and
permission modes), so there is nothing to install. Use the SDK wire format from an ACP client or an MCP
proxy you control: `dinoguard-hook goose` accepts the same JSON as `dinoguard-hook sdk`.
See docs/adapters/goose.md.
"""
from __future__ import annotations

from .sdk import SDKAdapter


class GooseAdapter(SDKAdapter):
    name = "goose"
