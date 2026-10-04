"""Angel One WebSocket 2.0 market-data feed with automatic reconnect.

Verified against the official SDK (``SmartApi/smartWebSocketV2.py``):

* ``SmartWebSocketV2(auth_token, api_key, client_code, feed_token, ...)``
* the SDK **already parses** the binary frame and calls ``on_data(ws, parsed)``
  with a dict whose ``last_traded_price`` is an integer in **paise**.
* ``subscribe(correlation_id, mode, token_list)`` where ``token_list`` is a list
  of ``{"exchangeType": int, "tokens": [str, ...]}``.

We run the SDK's blocking ``connect()`` on a dedicated thread and own the outer
reconnect loop (``max_retry_attempt=0``), handing decoded ticks to the asyncio
world via a thread-safe callback. Kept to the minimum work on the socket thread.
"""

from __future__ import annotations

import datetime as _dt
import threading
from collections.abc import Callable, Iterable

from ..config import Settings
from ..logging import get_logger
from .auth import AngelAuth

log = get_logger("angelone.websocket")

try:  # pragma: no cover
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2
except Exception:  # pragma: no cover
    from .auth import sdk_import_hint

    SmartWebSocketV2 = None  # type: ignore[assignment]

# callback(token: str, ltp: float, ts: datetime) -> None
TickCallback = Callable[[str, float, _dt.datetime], None]
StatusCallback = Callable[[str], None]


class AngelWebSocket:
    def __init__(
        self,
        settings: Settings,
        auth: AngelAuth,
        on_tick: TickCallback,
        on_status: StatusCallback | None = None,
    ) -> None:
        self.settings = settings
        self.auth = auth
        self.on_tick = on_tick
        self.on_status = on_status or (lambda _msg: None)

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._subscriptions: list[dict] = []
        self._ws = None
        self._connected = threading.Event()
        self._reconnect_delay = settings.ws_reconnect_initial_s

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._supervisor, name="angel-ws", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._close_ws()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    # ---------------------------------------------------------------- thread
    def _supervisor(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_once()
            except Exception as exc:  # pragma: no cover - network dependent
                log.warning("WebSocket error: %s", exc)
            self._connected.clear()
            if self._stop.is_set():
                break
            log.info("Reconnecting WebSocket in %.1fs", self._reconnect_delay)
            self._stop.wait(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, self.settings.ws_reconnect_max_s)

    def _run_once(self) -> None:
        if SmartWebSocketV2 is None:
            from .auth import sdk_import_hint

            raise RuntimeError(sdk_import_hint())
        session = self.auth.ensure_logged_in()

        # max_retry_attempt=0 -> the SDK will not self-reconnect; we own it.
        self._ws = SmartWebSocketV2(
            session.jwt_token,
            session.api_key,
            session.client_code,
            session.feed_token,
            max_retry_attempt=0,
        )
        self._ws.on_open = self._on_open
        self._ws.on_data = self._on_data
        self._ws.on_message = self._on_message
        self._ws.on_error = self._on_error
        self._ws.on_close = self._on_close

        log.info("Connecting WebSocket ...")
        self._ws.connect()  # blocking until the socket closes

    def _close_ws(self) -> None:
        ws = self._ws
        if ws is not None:
            try:
                ws.close_connection()
            except Exception:  # pragma: no cover
                pass

    # ------------------------------------------------------------ callbacks
    def _on_open(self, ws) -> None:
        self._connected.set()
        self._reconnect_delay = self.settings.ws_reconnect_initial_s
        log.info("WebSocket connected; subscribing to %d group(s)", len(self._subscriptions))
        self.on_status("connected")
        for group in list(self._subscriptions):
            self._subscribe_group(group)

    def _on_close(self, ws, *args) -> None:
        self._connected.clear()
        log.info("WebSocket closed")
        self.on_status("closed")

    def _on_error(self, *args) -> None:
        # Signature varies in the SDK (on_error("msg", "detail")); accept anything.
        log.warning("WebSocket error callback: %s", args)
        self.on_status("error")

    def _on_message(self, ws, message) -> None:
        # SDK calls on_message only for heartbeat/pong text messages.
        if isinstance(message, dict):
            self._handle_parsed(message)

    def _on_data(self, ws, message, *args) -> None:
        # SDK delivers an already-parsed dict here.
        if isinstance(message, dict):
            self._handle_parsed(message)
        elif isinstance(message, (bytes, bytearray)):
            self._handle_binary(bytes(message))

    # ------------------------------------------------------------- subscribe
    def subscribe(self, groups: Iterable[dict]) -> None:
        """``groups`` are Angel-format dicts: {"exchangeType": int, "tokens": [...]}."""
        with self._lock:
            for g in groups:
                if g not in self._subscriptions:
                    self._subscriptions.append(g)
        if self.connected:
            for g in groups:
                self._subscribe_group(g)

    def _subscribe_group(self, group: dict) -> None:
        try:
            if self._ws is not None:
                self._ws.subscribe("bn_ticks", self.settings.ws_mode, [group])
        except Exception as exc:  # pragma: no cover
            log.warning("Subscribe failed for %s: %s", group, exc)

    # --------------------------------------------------------------- decoding
    def _handle_parsed(self, parsed: dict) -> None:
        token = str(parsed.get("token") or parsed.get("symboltoken") or "")
        if not token:
            return
        raw = parsed.get("last_traded_price")
        if raw is None:
            raw = parsed.get("ltp")
        if raw is None:
            return
        ltp = float(raw)
        if ltp > 1000:  # wire format is paise
            ltp /= 100.0
        ts = _dt.datetime.now()
        et = parsed.get("exchange_timestamp")
        if isinstance(et, (int, float)) and et > 0:
            try:
                ts = _dt.datetime.fromtimestamp(et / 1000.0, tz=_dt.timezone.utc).astimezone()
            except (OverflowError, OSError, ValueError):
                pass
        self.on_tick(token, ltp, ts)

    def _handle_binary(self, data: bytes) -> None:
        """Fallback for SDK versions that hand us a raw frame (LTP layout)."""
        try:
            token = data[2:27].split(b"\x00", 1)[0].decode("ascii", "ignore")
            ts_ms = int.from_bytes(data[35:43], "little", signed=True)
            ltp = int.from_bytes(data[43:51], "little", signed=True) / 100.0
            if token and ltp > 0:
                ts = (
                    _dt.datetime.fromtimestamp(ts_ms / 1000.0, tz=_dt.timezone.utc).astimezone()
                    if ts_ms > 0
                    else _dt.datetime.now()
                )
                self.on_tick(token, ltp, ts)
        except (IndexError, ValueError) as exc:
            log.debug("Binary decode failed (%s); len=%d", exc, len(data))
