"""Pure indicator helpers (no I/O, no state)."""

from __future__ import annotations

from collections.abc import Sequence


def sma(values: Sequence[float], period: int) -> float | None:
    """Simple moving average of the last ``period`` values, or None if short."""
    if period < 2 or len(values) < period:
        return None
    window = values[-period:]
    return sum(window) / float(period)


def percent_move(base: float, current: float) -> float:
    """Signed percentage move from ``base`` to ``current``."""
    if base == 0:
        return 0.0
    return (current - base) / base * 100.0
