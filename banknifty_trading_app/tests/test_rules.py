"""Unit tests for individual rules."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from banknifty_trading_app.core.models import DayLevels, Side
from banknifty_trading_app.core.state import StrategyState
from banknifty_trading_app.rules import load_spec
from banknifty_trading_app.rules.base import RuleContext
from banknifty_trading_app.rules.entries import ReferenceCrossEntry
from banknifty_trading_app.rules.filters import SmaFilter
from banknifty_trading_app.rules.stops import DailyCloseStop
from banknifty_trading_app.rules.targets import PercentPartialTarget

SPEC_PATH = Path(__file__).resolve().parents[2] / "strategy_versions" / "v1_baseline.yaml"


def _ctx(spot, *, ref=100.0, prev=95.0, sma=90.0, state=None):
    state = state or StrategyState()
    return (
        RuleContext(
            ts=_dt.datetime(2026, 1, 5, 10, 0),
            trading_day=_dt.date(2026, 1, 5),
            spot=spot,
            futures=spot + 40,
            ref_close=ref,
            prev_close=prev,
            sma=sma,
            state=state,
        ),
        state,
    )


def test_sma_filter_allows_correct_sides():
    flt = SmaFilter(period=20)
    buy_ctx, _ = _ctx(120.0, sma=100.0)
    sell_ctx, _ = _ctx(80.0, sma=100.0)
    assert flt.allow(Side.BUY, buy_ctx)
    assert not flt.allow(Side.SELL, buy_ctx)
    assert flt.allow(Side.SELL, sell_ctx)
    assert not flt.allow(Side.BUY, sell_ctx)


def test_sma_filter_blocks_when_missing():
    flt = SmaFilter(period=20, block_when_missing=True)
    ctx, _ = _ctx(120.0, sma=None)
    assert not flt.allow(Side.BUY, ctx)


def test_reference_cross_requires_fresh_cross():
    entry = ReferenceCrossEntry(require_fresh_cross=True)
    ctx, state = _ctx(98.0)          # below the reference first
    entry.observe(ctx)
    assert entry.signal(ctx) is None  # nothing yet

    ctx2, _ = _ctx(101.0, state=state)  # now crosses above
    entry.observe(ctx2)
    assert entry.signal(ctx2) is Side.BUY


def test_reference_cross_gap_does_not_count_by_default():
    entry = ReferenceCrossEntry(require_fresh_cross=True, gap_open_counts=False)
    ctx, state = _ctx(105.0)  # gap open above the level
    entry.observe(ctx)
    assert entry.signal(ctx) is None


def test_daily_close_stop_ratchets_for_long():
    stop = DailyCloseStop(ratchet=True)
    ctx, _ = _ctx(110.0, prev=100.0)
    assert stop.daily_stop(Side.BUY, ctx, 95.0) == 100.0   # tighter
    assert stop.daily_stop(Side.BUY, ctx, 105.0) == 105.0  # never loosens
    assert stop.breached(Side.BUY, 99.0, 100.0)
    assert not stop.breached(Side.BUY, 101.0, 100.0)


def test_percent_partial_target_from_entry():
    target = PercentPartialTarget(percent=1.7, lots_to_exit=1)
    ctx, state = _ctx(101.0)
    state.side = Side.BUY
    state.lots_open = 2
    state.entry_spot = 100.0
    assert not target.should_trigger(ctx)  # +1.0% < 1.7%
    ctx2, _ = _ctx(102.0, state=state)
    assert target.should_trigger(ctx2)     # +2.0% >= 1.7%


def test_load_baseline_spec():
    spec = load_spec(SPEC_PATH)
    assert spec.version == "v1_baseline"
    assert spec.sma_period == 20
    assert spec.instrument.lots == 2
    assert spec.reference_offset_days == 2
    assert type(spec.partial).__name__ == "PercentPartialTarget"
