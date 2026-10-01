"""Mutable strategy state.

Kept deliberately small and flat: it lives in memory and is touched on every
tick, so it must be cheap to update and cheap to reset at cycle boundaries.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from .models import DayLevels, Side


@dataclass
class StrategyState:
    # position
    side: Side | None = None
    lots_open: int = 0
    entry_spot: float = 0.0
    entry_futures: float = 0.0
    entry_time: _dt.datetime | None = None
    entry_reason: str = ""

    # risk levels (all tracked on SPOT)
    stop_level: float | None = None
    initial_stop: float | None = None
    partial_taken: bool = False

    # favourable extreme reached since entry (peak for long, trough for short)
    peak_spot: float = 0.0
    trough_spot: float = 0.0

    # cross detection (reset each trading day)
    below_ref_seen: bool = False
    above_ref_seen: bool = False

    # bookkeeping
    initial_trade_done: bool = False
    cycle_closed: bool = False
    cycle_id: str = ""
    trading_day: _dt.date | None = None
    is_last_trading_day: bool = False

    # order flow guard
    pending_intent: str | None = None

    @property
    def has_position(self) -> bool:
        return self.lots_open > 0 and self.side is not None

    def reset_position(self) -> None:
        self.side = None
        self.lots_open = 0
        self.entry_spot = 0.0
        self.entry_futures = 0.0
        self.entry_time = None
        self.entry_reason = ""
        self.stop_level = None
        self.initial_stop = None
        self.partial_taken = False
        self.peak_spot = 0.0
        self.trough_spot = 0.0

    def start_new_cycle(self, cycle_id: str) -> None:
        self.reset_position()
        self.initial_trade_done = False
        self.cycle_closed = False
        self.cycle_id = cycle_id
        self.below_ref_seen = False
        self.above_ref_seen = False

    def reset_day_crosses(self) -> None:
        self.below_ref_seen = False
        self.above_ref_seen = False


@dataclass
class EngineMemory:
    """Non-positional, diagnostic memory of the last levels seen."""

    levels: DayLevels | None = None
    last_spot: float = 0.0
    last_futures: float = 0.0
    last_tick_time: _dt.datetime | None = None
    day_events: list[str] = field(default_factory=list)
