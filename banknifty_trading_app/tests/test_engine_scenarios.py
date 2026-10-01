"""Scenario tests for the strategy engine state machine."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from banknifty_trading_app.core.models import DayLevels, IntentKind, Side
from banknifty_trading_app.rules import load_spec
from banknifty_trading_app.strategy.engine import StrategyEngine

SPEC_PATH = Path(__file__).resolve().parents[2] / "strategy_versions" / "v1_baseline.yaml"
DAY = _dt.date(2026, 1, 5)


def _levels(ref=100.0, prev=95.0, sma=90.0):
    return DayLevels(trading_date=DAY, ref_close=ref, prev_close=prev, sma=sma)


def _ts(h, m=0):
    return _dt.datetime(DAY.year, DAY.month, DAY.day, h, m)


def _engine(reversal=True):
    spec = load_spec(SPEC_PATH)
    spec.reversal_enabled = reversal
    engine = StrategyEngine(spec)
    engine.start_new_cycle()
    return engine


def test_long_entry_partial_then_reversal():
    engine = _engine()
    engine.start_day(_levels(), first_spot=98.0, first_futures=138.0, ts=_ts(9, 15))

    assert engine.on_tick(99.0, 139.0, _ts(9, 20)) == []

    entry = engine.on_tick(101.0, 141.0, _ts(9, 25))
    assert len(entry) == 1
    assert entry[0].kind is IntentKind.ENTER and entry[0].side is Side.BUY
    assert engine.state.lots_open == 2
    assert engine.state.stop_level == 95.0  # yesterday's close on the entry day

    partial = engine.on_tick(103.0, 143.0, _ts(9, 30))
    assert len(partial) == 1 and partial[0].kind is IntentKind.PARTIAL_EXIT
    assert partial[0].lots == 1
    assert engine.state.lots_open == 1
    assert engine.state.stop_level == 101.0  # moved to breakeven

    reversal = engine.on_tick(100.0, 140.0, _ts(9, 35))
    kinds = [i.kind for i in reversal]
    assert IntentKind.EXIT in kinds
    assert IntentKind.ENTER in kinds
    short = [i for i in reversal if i.kind is IntentKind.ENTER][0]
    assert short.side is Side.SELL and short.lots == 2
    assert engine.state.side is Side.SELL


def test_short_entry_below_sma():
    engine = _engine()
    engine.start_day(_levels(sma=105.0), first_spot=102.0, first_futures=142.0, ts=_ts(9, 15))
    assert engine.on_tick(102.0, 142.0, _ts(9, 20)) == []  # above ref, above sma -> no short
    entry = engine.on_tick(99.0, 139.0, _ts(9, 25))
    assert len(entry) == 1
    assert entry[0].side is Side.SELL


def test_only_one_initial_trade_per_cycle():
    engine = _engine(reversal=False)
    engine.start_day(_levels(), first_spot=98.0, first_futures=138.0, ts=_ts(9, 15))
    engine.on_tick(101.0, 141.0, _ts(9, 25))          # open long
    stopped = engine.on_tick(94.0, 134.0, _ts(9, 40))  # stop hit
    assert len(stopped) == 1 and stopped[0].kind is IntentKind.EXIT
    assert engine.state.lots_open == 0

    again = engine.on_tick(106.0, 146.0, _ts(10, 0))  # would be a new cross
    assert again == []  # blocked: one initial trade per cycle


def test_expiry_square_off():
    engine = _engine()
    engine.start_day(_levels(), first_spot=98.0, first_futures=138.0, is_last_trading_day=True, ts=_ts(9, 15))
    engine.on_tick(101.0, 141.0, _ts(9, 25))  # open long
    assert engine.state.lots_open == 2

    square = engine.on_tick(101.5, 141.5, _ts(15, 16))  # after 15:15 square-off
    assert len(square) == 1 and square[0].kind is IntentKind.EXIT
    assert engine.state.lots_open == 0
    assert engine.state.cycle_closed is True


def test_protective_stop_avoids_instant_stop():
    # yesterday's close (103) is above the long entry, so it cannot be support
    engine = _engine()
    engine.start_day(_levels(prev=103.0), first_spot=99.0, first_futures=139.0, ts=_ts(9, 15))
    entry = engine.on_tick(101.0, 141.0, _ts(9, 25))
    assert entry[0].kind is IntentKind.ENTER
    assert engine.state.stop_level == 101.0  # falls back to entry, not 103


def test_reversal_cap_stops_churn():
    engine = _engine()
    engine.spec.reversal_cooldown_s = 0.0
    engine.spec.max_reversals_per_day = 1
    engine.start_day(_levels(), first_spot=98.0, first_futures=138.0, ts=_ts(9, 15))
    engine.on_tick(101.0, 141.0, _ts(9, 25))            # long
    first = engine.on_tick(94.0, 134.0, _ts(9, 40))     # stop -> reverse to short
    assert any(i.kind is IntentKind.ENTER for i in first)
    second = engine.on_tick(96.0, 136.0, _ts(10, 5))    # stop again -> cap reached
    kinds = [i.kind for i in second]
    assert IntentKind.EXIT in kinds
    assert IntentKind.ENTER not in kinds
