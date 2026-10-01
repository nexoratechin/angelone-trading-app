"""Stale market-data detection.

Feeds must not trade on frozen data. A monitor records the last tick time and,
if nothing arrives for longer than the timeout *while the market is open*,
raises a health event so risk/the kill switch can react.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable

from ..logging import get_logger

log = get_logger("market_data.staleness")


class StalenessMonitor:
    def __init__(
        self,
        timeout_s: float,
        on_stale: Callable[[], None] | None = None,
        now_fn: Callable[[], _dt.datetime] | None = None,
    ) -> None:
        self.timeout_s = float(timeout_s)
        self.on_stale = on_stale or (lambda: None)
        self._now = now_fn or _dt.datetime.now
        self._last_tick: _dt.datetime | None = None
        self._stale = False

    def mark(self, ts: _dt.datetime | None = None) -> None:
        self._last_tick = ts or self._now()
        self._stale = False

    def is_stale(self) -> bool:
        if self._last_tick is None:
            return True
        return (self._now() - self._last_tick).total_seconds() > self.timeout_s

    def check(self) -> bool:
        """Returns True once per transition into staleness."""
        stale = self.is_stale()
        if stale and not self._stale:
            self._stale = True
            log.error("Market data is stale (> %.1fs without a tick)", self.timeout_s)
            self.on_stale()
            return True
        return False
