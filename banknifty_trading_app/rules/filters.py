"""Entry filters."""

from __future__ import annotations

import datetime as _dt

from ..core.models import Side
from .base import Filter, RuleContext
from .registry import register_filter


@register_filter("sma")
class SmaFilter(Filter):
    """Allow BUY only above the SMA, SELL only below it.

    The SMA value itself is computed ahead of time from daily Spot closes (see
    ``market_data.daily_closes``) and supplied through ``RuleContext.sma``.

    ``period`` is part of the rule so switching 20 -> 30 is a YAML edit; the
    data layer is told which period to precompute from the same spec.
    """

    def __init__(self, period: int = 20, block_when_missing: bool = True) -> None:
        if period < 2:
            raise ValueError("SMA period must be >= 2")
        self.period = period
        self.block_when_missing = block_when_missing

    def allow(self, side: Side, ctx: RuleContext) -> bool:
        if ctx.sma is None:
            return not self.block_when_missing
        return ctx.spot > ctx.sma if side is Side.BUY else ctx.spot < ctx.sma


@register_filter("time_window")
class TimeWindowFilter(Filter):
    """Only allow new entries between ``start`` and ``end`` (inclusive start)."""

    def __init__(self, start: str = "09:15", end: str = "15:00") -> None:
        self.start = _dt.time.fromisoformat(start)
        self.end = _dt.time.fromisoformat(end)

    def allow(self, side: Side, ctx: RuleContext) -> bool:
        t = ctx.ts.timetz().replace(tzinfo=None)
        return self.start <= t <= self.end


@register_filter("min_distance")
class MinDistanceFilter(Filter):
    """Require Spot to be at least ``points`` away from the reference level.

    Useful to avoid entering on a micro-cross right at the line.
    """

    def __init__(self, points: float = 0.0) -> None:
        self.points = float(points)

    def allow(self, side: Side, ctx: RuleContext) -> bool:
        if self.points <= 0:
            return True
        if side is Side.BUY:
            return (ctx.spot - ctx.ref_close) >= self.points
        return (ctx.ref_close - ctx.spot) >= self.points
