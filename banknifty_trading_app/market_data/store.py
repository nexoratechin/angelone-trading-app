"""In-memory market-data store.

The hot path updates this and nothing else. No database, no files, no locks
beyond the GIL-atomic dict assignment. Daily closes are seeded at start-up and
appended once per day.
"""

from __future__ import annotations

import threading
from collections import deque

from ..core.models import Tick


class MarketDataStore:
    def __init__(self, history: int = 5_000) -> None:
        self._latest: dict[str, Tick] = {}
        self._history: dict[str, deque[Tick]] = {}
        self._closes: dict[str, list[float]] = {}
        self._history_len = history
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ ticks
    def update(self, tick: Tick) -> None:
        self._latest[tick.token] = tick
        buf = self._history.get(tick.token)
        if buf is None:
            buf = deque(maxlen=self._history_len)
            self._history[tick.token] = buf
        buf.append(tick)

    def latest(self, token: str) -> Tick | None:
        return self._latest.get(token)

    def ltp(self, token: str) -> float | None:
        tick = self._latest.get(token)
        return tick.ltp if tick else None

    def history(self, token: str) -> list[Tick]:
        return list(self._history.get(token, ()))

    # ---------------------------------------------------------- daily closes
    def set_closes(self, symbol: str, closes: list[float]) -> None:
        with self._lock:
            self._closes[symbol] = list(closes)

    def closes(self, symbol: str) -> list[float]:
        with self._lock:
            return list(self._closes.get(symbol, ()))

    def append_close(self, symbol: str, close: float) -> None:
        with self._lock:
            self._closes.setdefault(symbol, []).append(float(close))
