"""SQLite connection (WAL) + forward-only migrations tracked in PRAGMA user_version."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .. import paths

MIGRATIONS = Path(__file__).parent / "migrations"


def connect(path: str | Path | None = None, readonly: bool = False) -> sqlite3.Connection:
    p = Path(path) if path else paths.db_path()
    if readonly:
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=5, check_same_thread=False)
    else:
        conn = sqlite3.connect(str(p), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    if not readonly:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        conn.execute("PRAGMA synchronous=NORMAL")
        migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    files = sorted(MIGRATIONS.glob("*.sql"))
    for f in files:
        num = int(f.name.split("_", 1)[0])
        if num <= current:
            continue
        for attempt in range(5):
            try:
                conn.executescript(f.read_text(encoding="utf-8"))
                conn.execute(f"PRAGMA user_version={num}")
                conn.commit()
                break
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) or attempt == 4:
                    raise
                time.sleep(0.2)
        current = num
    return current


def rows(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn: sqlite3.Connection, sql: str, params=()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None
