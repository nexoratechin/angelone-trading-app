"""Live market-data feed: Angel WebSocket -> store + event bus.

The WebSocket callback runs on a background thread. It does the minimum work
possible (normalise + publish) so the asyncio consumer sees a fresh tick with
as little delay as we can reasonably achieve.
"""

from __future__ import annotations

import datetime as _dt

from ..angelone.auth import AngelAuth
from ..angelone.constants import WS_EXCHANGE_TYPE
from ..angelone.websocket import AngelWebSocket
from ..config import Settings
from ..core.bus import EventBus
from ..core.events import Topic
from ..core.models import Tick
from ..logging import get_logger
from .store import MarketDataStore

log = get_logger("market_data.feed")


class MarketDataFeed:
    def __init__(
        self,
        settings: Settings,
        auth: AngelAuth,
        bus: EventBus,
        store: MarketDataStore,
        subscriptions: list[tuple[str, str]],  # (exchange, token)
        token_symbols: dict[str, str],
    ) -> None:
        self.settings = settings
        self.bus = bus
        self.store = store
        self.subscriptions = subscriptions
        self.token_symbols = token_symbols
        self._ws = AngelWebSocket(settings, auth, self._on_tick, self._on_status)
        self.last_tick_time: _dt.datetime | None = None

    def start(self) -> None:
        groups: list[dict] = []
        by_exchange: dict[str, list[str]] = {}
        for exchange, token in self.subscriptions:
            by_exchange.setdefault(exchange, []).append(token)
        for exchange, tokens in by_exchange.items():
            ex_type = WS_EXCHANGE_TYPE.get(exchange.upper())
            if ex_type is None:
                log.warning("Unknown exchange %r - skipping subscription", exchange)
                continue
            groups.append({"exchangeType": ex_type, "tokens": tokens})
        self._ws.subscribe(groups)
        self._ws.start()
        log.info("Market data feed started for %d token(s)", len(self.subscriptions))

    def stop(self) -> None:
        self._ws.stop()

    @property
    def connected(self) -> bool:
        return self._ws.connected

    # ------------------------------------------------------------- callback
    def _on_tick(self, token: str, ltp: float, ts: _dt.datetime) -> None:
        # Runs on the WebSocket thread: keep it tiny, never block.
        symbol = self.token_symbols.get(token, token)
        tick = Tick(symbol=symbol, token=token, ltp=ltp, timestamp=ts)
        self.last_tick_time = ts
        self.store.update(tick)
        self.bus.publish_threadsafe(Topic.TICK, tick)

    def _on_status(self, status: str) -> None:
        self.bus.publish_threadsafe(Topic.HEALTH, {"source": "feed", "status": status})
