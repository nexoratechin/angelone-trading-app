"""Tests for the live-trading safety interlock.

The guarantee under test: **no configuration reachable from .env, the CLI or
the web console can place a real order unless the operator has explicitly run
``main arm-live``** - and once disarmed, a live intent fails loudly rather than
silently degrading to paper.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from banknifty_trading_app.config import LiveTradingLocked, Settings
from banknifty_trading_app.execution.live import LiveExecutor
from banknifty_trading_app.execution.router import build_executor
from banknifty_trading_app.execution.paper import PaperExecutor


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = {
        "data_dir": tmp_path / "data",
        "log_dir": tmp_path / "logs",
        "report_dir": tmp_path / "reports",
        "strategy_dir": tmp_path / "strategy",
        "db_path": tmp_path / "data/test.sqlite3",
        "kill_switch_file": tmp_path / "data/KILL",
        "instrument_cache_path": tmp_path / "data/instruments.json",
        "auth_db_path": tmp_path / "data/users.sqlite3",
    }
    base.update(overrides)
    s = Settings(**base)
    s.ensure_dirs()
    return s


# ===================================================================== flags
def test_live_armed_defaults_to_false(tmp_path):
    assert _settings(tmp_path).live_armed is False


def test_live_requires_all_three_flags(tmp_path):
    # mode + live_trading alone is NOT enough - the arm switch is the gate
    s = _settings(tmp_path, mode="live", live_trading=True)
    assert s.is_live is False
    s.live_armed = True
    assert s.is_live is True


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"mode": "live"},
        {"live_trading": True},
        {"mode": "live", "live_trading": True},
        {"live_armed": True},
        {"mode": "live", "live_armed": True},
        {"live_trading": True, "live_armed": True},
    ],
)
def test_never_live_without_every_flag(tmp_path, overrides):
    s = _settings(tmp_path, **overrides)
    assert s.is_live is False
    assert s.live_block_reason()


def test_block_reason_names_the_arm_switch(tmp_path):
    s = _settings(tmp_path, mode="live", live_trading=True)
    assert "LIVE_ARMED" in (s.live_block_reason() or "")


def test_assert_live_allowed_raises_when_locked(tmp_path):
    with pytest.raises(LiveTradingLocked):
        _settings(tmp_path, mode="live", live_trading=True).assert_live_allowed()


def test_assert_live_allowed_passes_when_armed(tmp_path):
    _settings(tmp_path, mode="live", live_trading=True, live_armed=True).assert_live_allowed()


def test_live_requested_detects_intent(tmp_path):
    s = _settings(tmp_path, mode="live")
    assert s.live_requested is True
    assert _settings(tmp_path).live_requested is False


# =================================================================== routing
def test_router_returns_paper_by_default(tmp_path):
    s = _settings(tmp_path)
    assert isinstance(build_executor(s, store=object()), PaperExecutor)


def test_router_refuses_live_request_without_arm(tmp_path):
    """A live request that is not armed must raise, never silently go paper."""
    s = _settings(tmp_path, mode="live", live_trading=True)
    with pytest.raises(LiveTradingLocked):
        build_executor(s, store=object())


def test_router_refuses_armed_live_without_client(tmp_path):
    """Armed but mis-wired must fail, not fall back to paper."""
    s = _settings(tmp_path, mode="live", live_trading=True, live_armed=True)
    with pytest.raises(RuntimeError):
        build_executor(s, store=object())


def test_router_builds_live_when_fully_armed(tmp_path):
    s = _settings(tmp_path, mode="live", live_trading=True, live_armed=True)
    ex = build_executor(s, store=object(), rest=object(), instrument=object())
    assert isinstance(ex, LiveExecutor)


# ============================================================== live executor
def test_live_executor_cannot_be_constructed_when_locked(tmp_path):
    s = _settings(tmp_path, mode="live", live_trading=True)
    with pytest.raises(LiveTradingLocked):
        LiveExecutor(s, rest=object(), instrument=object())


def test_live_executor_refuses_orders_if_disarmed_mid_session(tmp_path):
    """Disarming while running must block the very next order."""
    s = _settings(tmp_path, mode="live", live_trading=True, live_armed=True)
    ex = LiveExecutor(s, rest=object(), instrument=object())
    assert ex._arm_ok() is True

    s.live_armed = False
    assert ex._arm_ok() is False

    from banknifty_trading_app.core.models import (
        IntentKind,
        OrderRequest,
        Side,
        TradeIntent,
    )

    req = OrderRequest(
        intent_id="i1", symbol="BANKNIFTY", token="1", side=Side.BUY,
        quantity=60, order_type="MARKET", product_type="NRML",
        price=None, reason="test_entry",
    )
    intent = TradeIntent(
        intent_id="i1", kind=IntentKind.ENTER, side=Side.BUY, lots=2,
        reason="test_entry", spot_price=50000.0, futures_price=50050.0,
        timestamp=datetime.now(), strategy_version="v1",
    )
    assert asyncio.run(ex.execute(req, intent)) is None


def test_env_without_arm_key_is_not_live(tmp_path, monkeypatch):
    """Even MODE=live + LIVE_TRADING=true in the environment cannot go live."""
    monkeypatch.setenv("MODE", "live")
    monkeypatch.setenv("LIVE_TRADING", "true")
    s = _settings(tmp_path)  # explicit kwargs override the environment
    assert s.is_live is False

    # now let the environment through without overriding
    s2 = Settings(
        mode="live", live_trading=True,
        data_dir=tmp_path / "d2", db_path=tmp_path / "d2/db.sqlite3",
    )
    assert s2.live_armed is False
    assert s2.is_live is False