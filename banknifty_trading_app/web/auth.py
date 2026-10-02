"""Cookie-based login for the web console.

Sessions are HMAC-signed with the standard library only - there is no extra
dependency and no server-side session store. The signing key is derived from
``APP_PASSWORD``, so rotating the password invalidates every existing session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

SESSION_COOKIE = "bn_session"
DEFAULT_TTL_SECONDS = 12 * 60 * 60  # 12 hours


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, payload: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256)
    return _b64e(mac.digest())


def auth_enabled(settings) -> bool:
    """True when ``APP_PASSWORD`` is configured on the given settings object."""
    return bool(settings and settings.login_enabled)


def create_session_token(secret: str, username: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Build a signed ``payload.signature`` token that expires in ``ttl_seconds``."""
    expiry = int(time.time()) + int(ttl_seconds)
    payload = _b64e(f"{username}|{expiry}".encode("utf-8"))
    return f"{payload}.{_sign(secret, payload)}"


def verify_session_token(secret: str, token: str | None) -> str | None:
    """Return the signed-in username when the token is valid, else ``None``."""
    if not secret or not token or "." not in token:
        return None
    payload, _, signature = token.partition(".")
    if not hmac.compare_digest(_sign(secret, payload), signature):
        return None
    try:
        username, _, expiry = _b64d(payload).decode("utf-8").rpartition("|")
        if not username or int(expiry) < int(time.time()):
            return None
    except (ValueError, UnicodeDecodeError):
        return None
    return username


def credentials_ok(settings, username: str, password: str) -> bool:
    """Constant-time check of a submitted username/password pair."""
    if not auth_enabled(settings):
        return False
    expected_user = settings.app_username or "admin"
    expected_pass = settings.secret(settings.app_password)
    user_ok = hmac.compare_digest((username or "").strip(), expected_user)
    pass_ok = hmac.compare_digest(password or "", expected_pass)
    return user_ok and pass_ok
