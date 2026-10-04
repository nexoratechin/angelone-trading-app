"""Typed application configuration loaded from environment / ``.env``.

Secrets (API key, client code, PIN, TOTP secret) are held as ``SecretStr`` so
they are never accidentally printed, logged, or serialised. Nothing here is
hard-coded to a real account.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["paper", "live"]


class LiveTradingLocked(RuntimeError):
    """Raised whenever anything tries to trade real money without the arm switch.

    This is the single exception the safety interlock raises. It is checked at
    three independent layers (config, executor router, and immediately before
    every broker call) so a single missed check cannot place a live order.
    """


class Settings(BaseSettings):
    """All runtime configuration in one place."""

    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,  # empty values in .env fall back to defaults
    )

    # --- runtime -----------------------------------------------------------
    mode: Mode = "paper"
    live_trading: bool = False
    # Master arm switch for real-money trading. Live is impossible unless this
    # is true, *even if* MODE=live and LIVE_TRADING=true. Toggle it with
    #   python -m banknifty_trading_app.main arm-live   /  disarm-live
    # Default stays false so a stray env var can never arm live trading.
    live_armed: bool = False
    active_strategy: str = "v1_baseline"

    # --- paths -------------------------------------------------------------
    strategy_dir: Path = Path("strategy_versions")
    data_dir: Path = Path("data")
    log_dir: Path = Path("logs")
    report_dir: Path = Path("reports")
    db_path: Path = Path("data/banknifty.sqlite3")
    kill_switch_file: Path = Path("data/KILL_SWITCH")
    instrument_cache_path: Path = Path("data/instruments.json")

    # --- Angel One credentials (never hard-code, never log) ----------------
    angel_api_key: SecretStr = SecretStr("")
    angel_client_code: SecretStr = SecretStr("")
    angel_pin: SecretStr = SecretStr("")
    angel_totp_secret: SecretStr = SecretStr("")

    # --- Angel One endpoints (verify against the official docs) ------------
    angel_rest_url: str = "https://apiconnect.angelone.in"
    angel_ws_url: str = "wss://smartapisocket.angelone.in/smart-stream"
    angel_instrument_master_url: str = (
        "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    )

    # --- client IP sent to Angel One ---------------------------------------
    # smartapi-python hardcodes its X-ClientPublicIP/X-ClientLocalIP headers to
    # a fixed value in a `finally` block, so by default Angel One sees someone
    # else's IP - which breaks an IP-whitelisted API key. We patch the SDK with
    # the real address before login. Leave BOTH blank to auto-detect; set them
    # explicitly if you have a static IP or auto-detection is blocked.
    client_public_ip: str = ""
    client_local_ip: str = ""

    # --- TLS trust ----------------------------------------------------------
    # Use the operating system certificate store instead of certifi's bundle.
    # Needed when a corporate proxy (Zscaler/Netskope/Fortinet...) re-signs TLS,
    # because its root CA is in the OS store but not in certifi - without this,
    # every HTTPS call fails with CERTIFICATE_VERIFY_FAILED. Verification stays
    # ON; this only changes which root CAs are trusted.
    use_system_trust_store: bool = True

    # --- execution ---------------------------------------------------------
    product_type: Literal["NRML", "MIS"] = "NRML"
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    market_protect_buffer_points: float = 0.5
    paper_slippage_points: float = 0.5
    tick_size: float = 0.05
    order_status_timeout_s: float = 10.0
    order_status_poll_interval_s: float = 0.25

    # --- risk --------------------------------------------------------------
    max_lots: int = 10
    max_daily_loss: float | None = None
    max_trades_per_day: int | None = None

    # --- market data feed --------------------------------------------------
    stale_tick_timeout_s: float = 5.0
    ws_reconnect_initial_s: float = 1.0
    ws_reconnect_max_s: float = 30.0
    ws_mode: int = 1  # 1 = LTP, 2 = Quote, 3 = SnapQuote

    # --- session (IST) -----------------------------------------------------
    market_open: str = "09:15"
    market_close: str = "15:30"
    expiry_squareoff_time: str = "15:15"
    timezone: str = "Asia/Kolkata"

    # --- backtest ----------------------------------------------------------
    backtest_start: str = ""
    backtest_end: str = ""
    backtest_interval: str = "ONE_MINUTE"
    backtest_intrabar: Literal["conservative", "optimistic"] = "conservative"

    # --- dashboard ---------------------------------------------------------
    dashboard_enabled: bool = True
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8080
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    web_token: SecretStr = SecretStr("")

    # --- web console login -------------------------------------------------
    # Leave APP_PASSWORD blank to run without a login page (e.g. trusted
    # localhost). Set it to require a signed-in session for the whole console.
    app_username: str = "admin"
    app_password: SecretStr = SecretStr("")
    # Accounts created via the register page live here (password hashes only).
    auth_db_path: Path = Path("data/users.sqlite3")
    # Required for sign-ups once the first account exists. Blank = closed.
    register_code: SecretStr = SecretStr("")

    # --- validation --------------------------------------------------------
    @field_validator("mode")
    @classmethod
    def _normalise_mode(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("max_daily_loss", "max_trades_per_day", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if v is None:
            return None
        if isinstance(v, str) and v.strip().lower() in ("", "none", "null", "nan"):
            return None
        return v

    # --- helpers -----------------------------------------------------------
    @property
    def is_live(self) -> bool:
        """True only when the operator explicitly opts into live trading.

        Requires *all three* flags: ``MODE=live``, ``LIVE_TRADING=true`` and the
        master arm ``LIVE_ARMED=true``. Anything missing fails closed to paper -
        the app never "half goes live".
        """
        return self.mode == "live" and self.live_trading and self.live_armed

    @property
    def live_requested(self) -> bool:
        """True when the operator asked for live mode, armed or not.

        Used to fail loudly instead of silently downgrading to paper.
        """
        return self.mode == "live" or self.live_trading

    def live_block_reason(self) -> str | None:
        """Why live trading is unavailable, or ``None`` when it is armed."""
        if self.is_live:
            return None
        missing: list[str] = []
        if not self.live_armed:
            missing.append("LIVE_ARMED=false (master arm switch)")
        if self.mode != "live":
            missing.append(f"MODE={self.mode!r} (need MODE=live)")
        if not self.live_trading:
            missing.append("LIVE_TRADING=false (need LIVE_TRADING=true)")
        return "live trading is LOCKED: " + ", ".join(missing)

    def assert_live_allowed(self) -> None:
        """Raise :class:`LiveTradingLocked` unless every live flag is set."""
        reason = self.live_block_reason()
        if reason:
            raise LiveTradingLocked(reason)

    @property
    def login_enabled(self) -> bool:
        """True when a console password is configured (login page active)."""
        return bool(self.app_password.get_secret_value())

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.log_dir,
            self.report_dir,
            self.strategy_dir,
        ):
            Path(path).mkdir(parents=True, exist_ok=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.auth_db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.kill_switch_file).parent.mkdir(parents=True, exist_ok=True)

    def strategy_path(self, version: str | None = None) -> Path:
        version = version or self.active_strategy
        name = version if version.endswith((".yaml", ".yml")) else f"{version}.yaml"
        return Path(self.strategy_dir) / name

    def secret(self, value: SecretStr) -> str:
        return value.get_secret_value()

    def redacted_credentials(self) -> dict[str, str]:
        """Safe-to-log view of credential presence (never the values)."""
        return {
            "angel_api_key": "set" if self.angel_api_key.get_secret_value() else "missing",
            "angel_client_code": "set" if self.angel_client_code.get_secret_value() else "missing",
            "angel_pin": "set" if self.angel_pin.get_secret_value() else "missing",
            "angel_totp_secret": "set" if self.angel_totp_secret.get_secret_value() else "missing",
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
