"""Executor routing: paper by default, live only on explicit opt-in.

This module is the *single chokepoint* that decides whether an order can ever
reach the broker. ``build_executor`` is the only place ``LiveExecutor`` is
constructed, and it fails closed:

* ``is_live`` is true only when MODE=live **and** LIVE_TRADING=true **and**
  the master arm LIVE_ARMED=true.
* if live was *requested* but the interlock is not satisfied, we raise instead
  of silently falling back to paper - an operator who asked for live must never
  quietly get paper fills while believing they are live.
"""

from __future__ import annotations

from ..angelone.instruments import Instrument
from ..angelone.rest import AngelREST
from ..config import LiveTradingLocked, Settings
from ..logging import get_logger
from ..market_data.store import MarketDataStore
from .live import LiveExecutor
from .paper import PaperExecutor

log = get_logger("execution.router")


def build_executor(
    settings: Settings,
    store: MarketDataStore,
    *,
    rest: AngelREST | None = None,
    instrument: Instrument | None = None,
):
    """Return the executor for this run, enforcing the live-trading interlock."""
    if settings.is_live:
        settings.assert_live_allowed()
        if rest is None or instrument is None:
            raise RuntimeError("Live mode requires an authenticated REST client and an instrument")
        log.warning("LIVE EXECUTOR ARMED - orders will be sent to the broker")
        return LiveExecutor(settings, rest, instrument)

    if settings.live_requested:
        # mode/live_trading set but LIVE_ARMED is false -> refuse loudly.
        raise LiveTradingLocked(settings.live_block_reason() or "live trading is locked")

    return PaperExecutor(settings, store)