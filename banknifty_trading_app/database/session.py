"""sqlite3 connection management (WAL, busy timeout) + schema init."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import SCHEMA


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def init_schema(self) -> None:
        conn = self.connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = self.connect()
        try:
            return list(conn.execute(sql, params))
        finally:
            conn.close()


def create_database(path: str | Path) -> Database:
    db = Database(path)
    db.init_schema()
    return db


# Backwards-compatible alias used by a couple of call sites / docs.
create_session_factory = create_database
