"""Angel One SmartAPI authentication (API key + client code + PIN + TOTP).

Uses only the official ``smartapi-python`` SDK. Credentials come from settings
(environment / ``.env``) and are never hard-coded or logged.

Verified against the official SDK source:

* ``SmartConnect.generateSession(clientCode, password, totp)`` returns the
  *profile* response with the tokens nested under ``data``::

      {"status": true, "data": {"jwtToken": "Bearer ...", "refreshToken": ...,
                                "feedToken": ..., "clientcode": ...}}

* the JWT is passed to the WebSocket as-is (the official sample passes the
  ``"Bearer ..."`` value), while the REST SDK adds its own ``Bearer`` prefix
  internally for REST calls.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..logging import get_logger

log = get_logger("angelone.auth")

try:  # pragma: no cover - import guard
    from SmartApi import SmartConnect
except Exception:  # pragma: no cover
    SmartConnect = None  # type: ignore[assignment]


@dataclass
class AngelSession:
    api_key: str
    client_code: str
    jwt_token: str = ""       # includes the "Bearer " prefix (used by the WebSocket)
    refresh_token: str = ""
    feed_token: str = ""
    profile: dict | None = None

    @property
    def is_valid(self) -> bool:
        return bool(self.jwt_token)


def _extract_tokens(data: dict) -> dict:
    """Handle both the real nested shape and a flat shape defensively."""
    if not data:
        return {}
    inner = data.get("data")
    if isinstance(inner, dict) and inner.get("jwtToken"):
        return inner
    return data


class AngelAuth:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = None
        self.session: AngelSession | None = None

    # ------------------------------------------------------------------ login
    def login(self) -> AngelSession:
        if SmartConnect is None:
            raise RuntimeError(
                "smartapi-python is not installed. Run: pip install smartapi-python"
            )

        api_key = self.settings.secret(self.settings.angel_api_key)
        client_code = self.settings.secret(self.settings.angel_client_code)
        pin = self.settings.secret(self.settings.angel_pin)
        totp_secret = self.settings.secret(self.settings.angel_totp_secret)

        if not all([api_key, client_code, pin, totp_secret]):
            raise RuntimeError(
                "Missing Angel One credentials. Set ANGEL_API_KEY, ANGEL_CLIENT_CODE, "
                "ANGEL_PIN and ANGEL_TOTP_SECRET in your .env file (or run: setup)."
            )

        import pyotp

        totp = pyotp.TOTP(totp_secret).now()
        self.client = SmartConnect(api_key=api_key)

        raw = self._generate_session(client_code, pin, totp)
        tokens = _extract_tokens(raw)
        jwt_token = tokens.get("jwtToken") or ""
        if not jwt_token:
            raise RuntimeError(
                "Angel One login failed (no jwtToken in response). Checks: "
                "credentials, system clock/TOTP, and IP whitelisting. "
                f"Response: {str(raw)[:300]}"
            )
        if not jwt_token.startswith("Bearer "):
            jwt_token = "Bearer " + jwt_token

        self.session = AngelSession(
            api_key=api_key,
            client_code=client_code,
            jwt_token=jwt_token,
            refresh_token=tokens.get("refreshToken", ""),
            feed_token=tokens.get("feedToken", ""),
        )
        self.session.profile = raw
        log.info("Logged in to Angel One as %s", client_code)
        return self.session

    def _generate_session(self, client_code: str, pin: str, totp: str) -> dict:
        """SDK is ``generateSession(clientCode, password, totp)`` (3 args)."""
        assert self.client is not None
        try:
            return self.client.generateSession(client_code, pin, totp)
        except TypeError:
            # very old SDKs took only (clientCode, password)
            return self.client.generateSession(client_code, pin)

    def ensure_logged_in(self) -> AngelSession:
        if self.session is None or not self.session.is_valid:
            return self.login()
        return self.session

    def logout(self) -> None:
        if self.client is not None and self.session is not None:
            try:
                self.client.terminateSession(self.session.client_code)
            except Exception as exc:  # pragma: no cover
                log.warning("Logout failed: %s", exc)
        self.session = None
