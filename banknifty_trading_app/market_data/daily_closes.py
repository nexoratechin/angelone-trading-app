"""Derive daily levels from a series of completed daily Spot closes.

Convention (all lists ascending, oldest first, most recent completed day last):

    closes = [..., close(T-2), close(T-1)]
                                    ^ prev_close  -> "yesterday"
                         ^ ref_close -> "day before yesterday" (entry trigger)

The SMA is computed from the most recent ``sma_period`` completed closes, so it
is fixed before the session and never uses future data.
"""

from __future__ import annotations

import datetime as _dt

from ..core.models import DayLevels
from ..strategy.indicators import sma


class InsufficientHistoryError(RuntimeError):
    pass


def compute_levels(
    trading_date: _dt.date,
    closes: list[float],
    *,
    reference_offset_days: int = 2,
    sma_period: int = 20,
    require_sma: bool = True,
) -> DayLevels:
    if reference_offset_days < 1:
        raise ValueError("reference_offset_days must be >= 1")
    needed = max(reference_offset_days, sma_period if require_sma else 1)
    if len(closes) < needed:
        raise InsufficientHistoryError(
            f"need at least {needed} daily closes, have {len(closes)}"
        )

    prev_close = float(closes[-1])
    ref_close = float(closes[-reference_offset_days])
    sma_value = sma(closes[-sma_period:], sma_period) if require_sma else None

    return DayLevels(
        trading_date=trading_date,
        ref_close=ref_close,
        prev_close=prev_close,
        sma=sma_value,
    )
