"""Phone approval: send "Approve? YES/NO", wait for the answer, default deny on NO or timeout.

Answers can arrive from: Telegram (polled with getUpdates), WhatsApp via the dashboard webhook
(/api/approvals/twilio), the dashboard button, or `dinoguard approve answer <code> yes|no` typed by the user.
"""
from __future__ import annotations

import re
import secrets as _secrets
import sqlite3
import time

from ..engine.decisions import ALLOW, DENY, Decision
from ..store.writer import stream_note, summarize
from ..util import new_id, now_iso

POLL_S = 1.0


def create(conn: sqlite3.Connection, event, reason: str, channel: str | None) -> dict:
    code = f"{_secrets.randbelow(10000):04d}"
    row = {"id": new_id("apr"), "event_id": event.id, "channel": channel, "sent_at": now_iso(), "code": code,
           "summary": f"{event.tool_name}: {summarize(event)}"[:300]}
    conn.execute("INSERT INTO approvals(id, event_id, channel, sent_at, code, summary) VALUES (?,?,?,?,?,?)",
                 (row["id"], row["event_id"], channel, row["sent_at"], code, row["summary"]))
    conn.commit()
    return row


def answer(conn: sqlite3.Connection, code_or_id: str, yes: bool) -> bool:
    cur = conn.execute("UPDATE approvals SET answer=?, answered_at=? WHERE (code=? OR id=?) AND answer IS NULL",
                       ("yes" if yes else "no", now_iso(), code_or_id, code_or_id))
    conn.commit()
    return cur.rowcount > 0


def pending(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM approvals WHERE answer IS NULL ORDER BY sent_at DESC")]


_REPLY = re.compile(r"(?i)^\s*(yes|y|approve|ok|no|n|deny|reject)\b\s*(\d{4})?")


def parse_reply(text: str) -> tuple[bool, str | None] | None:
    m = _REPLY.match(text or "")
    if not m:
        return None
    return m.group(1).lower() in ("yes", "y", "approve", "ok"), m.group(2)


def _poll_telegram(conn, row, state: dict) -> None:
    from ..alerts import channels
    try:
        updates = channels.telegram_updates(state.get("offset"), timeout_s=0)
    except Exception:  # noqa: BLE001
        return
    for u in updates:
        state["offset"] = u.get("update_id", 0) + 1
        text = ((u.get("message") or {}).get("text")) or ""
        parsed = parse_reply(text)
        if parsed and (parsed[1] in (None, row["code"])):
            answer(conn, row["code"], parsed[0])


def wait(conn: sqlite3.Connection, row: dict, timeout_s: int, channel_type: str | None) -> str | None:
    deadline = time.monotonic() + timeout_s
    state: dict = {}
    while time.monotonic() < deadline:
        r = conn.execute("SELECT answer FROM approvals WHERE id=?", (row["id"],)).fetchone()
        if r and r["answer"]:
            return r["answer"]
        if channel_type == "telegram":
            _poll_telegram(conn, row, state)
        time.sleep(POLL_S)
    return None


def make_approver(cfg: dict, conn: sqlite3.Connection, sender=None):
    """Return approver(event, hold_decision) -> Decision used by the engine for phone-approval holds."""
    def approver(event, d: Decision) -> Decision:
        acfg = cfg.get("approvals") or {}
        channel = acfg.get("channel")
        timeout = int(d.hold_timeout_s or acfg.get("timeout_s", 120))
        if not channel:
            return Decision(DENY, f"{d.reason}. Phone approval is not set up, so this is denied. Ask the user to run "
                                  "`dinoguard approve setup --telegram` (or --whatsapp), or to run the step themselves.",
                            policy=d.policy)
        row = create(conn, event, d.reason or "", channel)
        text = (f"DinoGuard approval #{row['code']}\n{row['summary']}\nWhy: {d.reason}\n"
                f"Reply YES {row['code']} or NO {row['code']} within {timeout}s.")
        ch_type = ((cfg.get("channels") or {}).get(channel) or {}).get("type", channel)
        try:
            if sender:
                sender(channel, text)
            else:
                from ..alerts.channels import send
                send(channel, text, cfg)
        except Exception as e:  # noqa: BLE001
            return Decision(DENY, f"{d.reason}. Could not send the approval request ({e}); denied.", policy=d.policy)
        stream_note("approval_wait", session=event.session_id, tool=event.tool_name, summary=row["summary"],
                    code=row["code"], timeout_s=timeout)
        ans = wait(conn, row, timeout, ch_type)
        stream_note("approval_done", session=event.session_id, answer=ans or "timeout", code=row["code"])
        if ans == "yes":
            return Decision(ALLOW, f"approved by human (#{row['code']})", policy=d.policy)
        return Decision(DENY, f"{d.reason}. The human {'declined' if ans == 'no' else 'did not answer in time'}; "
                              "do not retry this action - ask the user how to proceed.", policy=d.policy)
    return approver
