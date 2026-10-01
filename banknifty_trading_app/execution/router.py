"""Executor routing: paper by default, live only on explicit opt-in."""

from __future__ import annotations

from ..angelone.instruments import Instrument
from ..angelone.rest import AngelREST
from ..config import Settings
from ..market_data.store import MarketDataStore
from .live import LiveExecutor
from .paper import PaperExecutor


def build_executor(
    settings: Settings,
    store: MarketDataStore,
    *,
    rest: AngelREST | None = None,
    instrument: Instrument | None = None,
):
    if settings.is_live:
        if rest is None or instrument is None:
            raise RuntimeError("Live mode requires an authenticated REST client and an instrument")
        return LiveExecutor(settings, rest, instrument)
    return PaperExecutor(settings, store)
