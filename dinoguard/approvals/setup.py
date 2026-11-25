"""Set up approval / alert channels. Secrets go to secrets.json, never config.json."""
from __future__ import annotations

import getpass
import sys

from .. import config

SECRET_KEYS = {
    "telegram": {"bot_token": "telegram_bot_token"},
    "whatsapp": {"auth_token": "twilio_auth_token", "token": "meta_whatsapp_token"},
    "slack": {"webhook_url": "slack_webhook_url"},
    "email": {"password": "smtp_password"},
    "webhook": {"secret": None},
}


def save_channel(name: str, kind: str, values: dict) -> dict:
    values = dict(values)
    for field, secret_key in SECRET_KEYS.get(kind, {}).items():
        if field in values:
            v = str(values.pop(field))
            config.save_secret(secret_key or f"webhook_secret_{name}", v)
    ch = {"type": kind, **values}
    config.update(lambda c: c.setdefault("channels", {}).__setitem__(name, ch))
    return ch


def _ask(prompt: str, secret: bool = False) -> str:
    if not sys.stdin.isatty():
        return sys.stdin.readline().strip()
    return getpass.getpass(prompt) if secret else input(prompt).strip()


def interactive(kind: str, provider: str = "twilio", values: dict | None = None) -> dict:
    values = dict(values or {})
    if kind == "telegram":
        if "bot_token" not in values and not config.secret("telegram_bot_token", "TELEGRAM_BOT_TOKEN"):
            values["bot_token"] = _ask("Telegram bot token (from @BotFather): ", secret=True)
        values.setdefault("chat_id", values.get("chat_id") or _ask("Your chat id: "))
    elif kind == "whatsapp":
        values["provider"] = provider
        if provider == "twilio":
            values.setdefault("account_sid", values.get("account_sid") or _ask("Twilio account SID: "))
            if "auth_token" not in values and not config.secret("twilio_auth_token", "TWILIO_AUTH_TOKEN"):
                values["auth_token"] = _ask("Twilio auth token: ", secret=True)
            values.setdefault("from", values.get("from") or _ask("Twilio WhatsApp sender number (+1...): "))
        else:
            values.setdefault("phone_number_id", values.get("phone_number_id") or _ask("Meta phone number id: "))
            if "token" not in values and not config.secret("meta_whatsapp_token", "META_WHATSAPP_TOKEN"):
                values["token"] = _ask("Meta access token: ", secret=True)
        values.setdefault("to", values.get("to") or _ask("Your WhatsApp number (+...): "))
    ch = save_channel(kind, kind, values)
    config.update(lambda c: c.setdefault("approvals", {}).__setitem__("channel", kind))
    return ch
