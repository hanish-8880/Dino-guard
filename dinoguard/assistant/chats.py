"""Persist assistant conversations (chats + chat_messages)."""
from __future__ import annotations

import json

from ..util import new_id, now_iso


def record(conn, chat_id: str | None, question: str, res: dict) -> str:
    if not chat_id or not conn.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone():
        chat_id = new_id("chat")
        conn.execute("INSERT INTO chats(id, title, created_at) VALUES (?,?,?)", (chat_id, question[:80], now_iso()))
    conn.execute("INSERT INTO chat_messages(id, chat_id, role, content, sql, created_at) VALUES (?,?,?,?,?,?)",
                 (new_id("msg"), chat_id, "user", question, None, now_iso()))
    summary = {"columns": res.get("columns"), "rows": (res.get("rows") or [])[:20], "error": res.get("error")}
    conn.execute("INSERT INTO chat_messages(id, chat_id, role, content, sql, created_at) VALUES (?,?,?,?,?,?)",
                 (new_id("msg"), chat_id, "assistant", json.dumps(summary, default=str), res.get("sql"), now_iso()))
    conn.commit()
    return chat_id
