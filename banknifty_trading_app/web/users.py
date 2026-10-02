"""SQLite-backed account store for the web console.

Passwords are hashed with PBKDF2-HMAC-SHA256 (standard library only) and a
per-user random salt. A per-store random ``session_secret`` is generated once
and persisted so signed-in cookies survive server restarts.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
import re
import sqlite3
from pathlib import Path

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
MIN_PASSWORD_LEN = 8
_PBKDF2_ITERATIONS = 240_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def validate_username(username: str) -> str:
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise ValueError("username must be 3-32 characters: letters, digits, '.', '_' or '-'")
    return username


def validate_password(password: str) -> str:
    if not password or len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    return password


def _hash_password(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), _PBKDF2_ITERATIONS
    ).hex()


class UserStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure()

    # ------------------------------------------------------------------ plumbing
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _ensure(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ accounts
    def count(self) -> int:
        conn = self._connect()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        finally:
            conn.close()

    def get(self, username: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list(self) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT username, role, created_at FROM users ORDER BY created_at"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def create(self, username: str, password: str, role: str | None = None) -> dict:
        username = validate_username(username)
        validate_password(password)
        if self.get(username):
            raise ValueError("that username is already taken")
        role = role or ("admin" if self.count() == 0 else "user")
        salt = os.urandom(16).hex()
        password_hash = _hash_password(password, salt)
        created_at = _dt.datetime.now().isoformat(timespec="seconds")
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, salt, role, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (username, password_hash, salt, role, created_at),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:  # racing insert
            raise ValueError("that username is already taken") from exc
        finally:
            conn.close()
        return {"username": username, "role": role, "created_at": created_at}

    def verify(self, username: str, password: str) -> dict | None:
        row = self.get(username)
        if row is None:
            # burn comparable time so missing users are not obviously faster
            _hash_password(password or "", "00" * 16)
            return None
        candidate = _hash_password(password or "", row["salt"])
        if hmac.compare_digest(candidate, row["password_hash"]):
            return {"username": row["username"], "role": row["role"]}
        return None

    # -------------------------------------------------------------------- meta
    def secret(self) -> str:
        """Stable random key used to sign session cookies (created on demand)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'session_secret'"
            ).fetchone()
            if row:
                return row["value"]
            value = os.urandom(32).hex()
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('session_secret', ?)", (value,)
            )
            conn.commit()
            return value
        finally:
            conn.close()
