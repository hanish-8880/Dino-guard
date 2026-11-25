"""Outbound notification channels: WhatsApp (Twilio / Meta Cloud API), Telegram, Slack, email, signed webhook.

Channel config lives in config["channels"][name] = {"type": ..., ...}; credentials in secrets.json.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import smtplib
import time
from email.message import EmailMessage
from typing import Any

from .. import config as config_mod

TIMEOUT = 10.0


class ChannelError(RuntimeError):
    pass


def _httpx():
    import httpx
    return httpx


def send_whatsapp(cfg: dict[str, Any], text: str) -> dict:
    provider = cfg.get("provider", "twilio")
    h = _httpx()
    if provider == "twilio":
        sid = cfg.get("account_sid") or config_mod.secret("twilio_account_sid", "TWILIO_ACCOUNT_SID")
        token = config_mod.secret("twilio_auth_token", "TWILIO_AUTH_TOKEN")
        if not (sid and token and cfg.get("from") and cfg.get("to")):
            raise ChannelError("twilio whatsapp needs account_sid, auth token, from, to")
        r = h.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", auth=(sid, token),
                   data={"From": f"whatsapp:{cfg['from']}", "To": f"whatsapp:{cfg['to']}", "Body": text},
                   timeout=TIMEOUT)
    elif provider == "meta":
        token = config_mod.secret("meta_whatsapp_token", "META_WHATSAPP_TOKEN")
        if not (token and cfg.get("phone_number_id") and cfg.get("to")):
            raise ChannelError("meta whatsapp needs token, phone_number_id, to")
        r = h.post(f"https://graph.facebook.com/v20.0/{cfg['phone_number_id']}/messages",
                   headers={"Authorization": f"Bearer {token}"},
                   json={"messaging_product": "whatsapp", "to": cfg["to"], "type": "text", "text": {"body": text}},
                   timeout=TIMEOUT)
    else:
        raise ChannelError(f"unknown whatsapp provider {provider!r}")
    if r.status_code >= 300:
        raise ChannelError(f"whatsapp send failed: HTTP {r.status_code} {r.text[:200]}")
    return r.json() if r.text else {}


def telegram_token() -> str | None:
    return config_mod.secret("telegram_bot_token", "TELEGRAM_BOT_TOKEN")


def send_telegram(cfg: dict[str, Any], text: str) -> dict:
    token = telegram_token()
    if not (token and cfg.get("chat_id")):
        raise ChannelError("telegram needs bot token and chat_id")
    r = _httpx().post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": cfg["chat_id"], "text": text}, timeout=TIMEOUT)
    if r.status_code >= 300:
        raise ChannelError(f"telegram send failed: HTTP {r.status_code}")
    return r.json()


def telegram_updates(offset: int | None, timeout_s: int = 0) -> list[dict]:
    token = telegram_token()
    if not token:
        return []
    params = {"timeout": timeout_s}
    if offset is not None:
        params["offset"] = offset
    r = _httpx().get(f"https://api.telegram.org/bot{token}/getUpdates", params=params, timeout=TIMEOUT + timeout_s)
    if r.status_code >= 300:
        return []
    return r.json().get("result", [])


def send_slack(cfg: dict[str, Any], text: str) -> dict:
    url = cfg.get("webhook_url") or config_mod.secret("slack_webhook_url", "SLACK_WEBHOOK_URL")
    if not url:
        raise ChannelError("slack needs webhook_url")
    r = _httpx().post(url, json={"text": text}, timeout=TIMEOUT)
    if r.status_code >= 300:
        raise ChannelError(f"slack send failed: HTTP {r.status_code}")
    return {}


def send_email(cfg: dict[str, Any], text: str, subject: str = "DinoGuard") -> dict:
    host, to = cfg.get("smtp_host"), cfg.get("to")
    if not (host and to):
        raise ChannelError("email needs smtp_host and to")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, cfg.get("from", "dinoguard@localhost"), to
    msg.set_content(text)
    with smtplib.SMTP(host, int(cfg.get("smtp_port", 587)), timeout=TIMEOUT) as s:
        if cfg.get("starttls", True):
            s.starttls()
        user = cfg.get("username")
        pw = config_mod.secret("smtp_password", "DG_SMTP_PASSWORD")
        if user and pw:
            s.login(user, pw)
        s.send_message(msg)
    return {}


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def send_webhook(cfg: dict[str, Any], text: str, payload: dict | None = None) -> dict:
    url = cfg.get("url")
    if not url:
        raise ChannelError("webhook needs url")
    body = json.dumps({"text": text, "ts": int(time.time()), **(payload or {})}).encode()
    headers = {"Content-Type": "application/json"}
    sec = config_mod.secret(f"webhook_secret_{cfg.get('name', 'default')}") or cfg.get("secret")
    if sec:
        headers["X-DinoGuard-Signature"] = sign(body, sec)
    r = _httpx().post(url, content=body, headers=headers, timeout=TIMEOUT)
    if r.status_code >= 300:
        raise ChannelError(f"webhook failed: HTTP {r.status_code}")
    return {}


SENDERS = {"whatsapp": send_whatsapp, "telegram": send_telegram, "slack": send_slack, "email": send_email,
           "webhook": send_webhook}


def send(channel_name: str, text: str, cfg: dict | None = None, **kw) -> dict:
    cfg = cfg or config_mod.load()
    ch = (cfg.get("channels") or {}).get(channel_name)
    if not ch:
        raise ChannelError(f"channel {channel_name!r} is not configured (dinoguard approve setup / settings)")
    fn = SENDERS.get(ch.get("type", channel_name))
    if not fn:
        raise ChannelError(f"unknown channel type {ch.get('type')!r}")
    if fn is send_email:
        return fn({**ch, "name": channel_name}, text, kw.get("subject", "DinoGuard"))
    return fn({**ch, "name": channel_name}, text)
