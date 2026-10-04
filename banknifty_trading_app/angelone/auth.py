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

SDK_IMPORT_ERROR: Exception | None = None

try:  # pragma: no cover - import guard
    from SmartApi import SmartConnect
except Exception as _exc:  # pragma: no cover
    # Keep the real reason. smartapi-python ships without declaring all of its
    # own dependencies (historically `logzero`, and `six` via SmartConnect), so
    # a bare ImportError here usually means a missing transitive package - NOT
    # that the user forgot to install smartapi-python. Surfacing the original
    # exception is the difference between a 5-second fix and an hour of guessing.
    SmartConnect = None  # type: ignore[assignment]
    SDK_IMPORT_ERROR = _exc


def sdk_import_hint() -> str:
    """Human-readable reason the Angel One SDK is unavailable."""
    if SDK_IMPORT_ERROR is None:
        return (
            "smartapi-python is not importable, but no import error was recorded. "
            "Run:  pip install -r requirements.txt"
        )
    exc = SDK_IMPORT_ERROR
    if isinstance(exc, ModuleNotFoundError) and exc.name:
        if exc.name == "SmartApi":
            return (
                "smartapi-python is not installed. Run:  pip install smartapi-python\n"
                "  (or install everything:  pip install -r requirements.txt)"
            )
        # A transitive dependency of the SDK is missing. smartapi-python does not
        # declare all of its own requirements, so name the exact package to add.
        return (
            f"smartapi-python is installed but its dependency '{exc.name}' is missing. "
            f"Fix with:  pip install {exc.name}\n"
            f"  (original error: {type(exc).__name__}: {exc})"
        )
    return (
        f"smartapi-python could not be imported: {type(exc).__name__}: {exc}\n"
        "  Fix with:  pip install -r requirements.txt"
    )


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
            raise RuntimeError(sdk_import_hint())

        api_key = self.settings.secret(self.settings.angel_api_key)
        client_code = self.settings.secret(self.settings.angel_client_code)
        pin = self.settings.secret(self.settings.angel_pin)
        totp_secret = self.settings.secret(self.settings.angel_totp_secret)

        if not all([api_key, client_code, pin, totp_secret]):
            raise RuntimeError(
                "Missing Angel One credentials. Set ANGEL_API_KEY, ANGEL_CLIENT_CODE, "
                "ANGEL_PIN and ANGEL_TOTP_SECRET in your .env file (or run: setup)."
            )

        # Must happen before the first request: smartapi-python otherwise sends a
        # hardcoded X-ClientPublicIP and IP-whitelisted keys get rejected.
        from .client_ip import patch_sdk_client_ip

        patch_sdk_client_ip(self.settings)

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
