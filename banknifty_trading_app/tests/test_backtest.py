"""Backtest, level computation and persistence tests."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from banknifty_trading_app.backtest.costs import CostModel
from banknifty_trading_app.backtest.replay import Backtester
from banknifty_trading_app.backtest.synthetic import generate
from banknifty_trading_app.config import Settings
from banknifty_trading_app.core.models import Side
from banknifty_trading_app.database.models import TradeRow
from banknifty_trading_app.database.repository import Repository
from banknifty_trading_app.database.session import create_database
from banknifty_trading_app.database.writer import PersistenceWriter
from banknifty_trading_app.market_data.daily_closes import compute_levels
from banknifty_trading_app.reports.excel import write_report
from banknifty_trading_app.rules import load_spec

SPEC_PATH = Path(__file__).resolve().parents[2] / "strategy_versions" / "v1_baseline.yaml"


def test_compute_levels_uses_day_before_yesterday_and_sma():
    closes = [float(i) for i in range(1, 26)]  # 1..25 ascending
    levels = compute_levels(_dt.date(2026, 1, 5), closes, reference_offset_days=2, sma_period=20)
    assert levels.prev_close == 25.0            # yesterday
    assert levels.ref_close == 24.0             # day before yesterday
    assert levels.sma == sum(range(6, 26)) / 20  # last 20 closes


def test_synthetic_backtest_is_deterministic(tmp_path):
    spec = load_spec(SPEC_PATH)
    start = _dt.date(2025, 1, 1)
    end = _dt.date(2025, 6, 30)

    def run_once():
        spot, fut, expiries = generate(start, end, seed=7)
        bt = Backtester(spec, cost_model=CostModel(), intrabar="conservative")
        return bt.run(spot, fut, expiries)

    r1 = run_once()
    r2 = run_once()
    assert len(r1.trades) == len(r2.trades)
    assert [t.pnl_money for t in r1.trades] == [t.pnl_money for t in r2.trades]
    assert len(r1.trades) > 0  # the strategy should trade over six months
    assert all(t.status == "CLOSED" for t in r1.trades)


def test_report_written(tmp_path):
    spec = load_spec(SPEC_PATH)
    spot, fut, expiries = generate(_dt.date(2025, 1, 1), _dt.date(2025, 4, 30), seed=11)
    result = Backtester(spec, cost_model=CostModel()).run(spot, fut, expiries)
    out = tmp_path / "report.xlsx"
    write_report(out, result.trades, events=result.events, meta=result.meta)
    assert out.exists() and out.stat().st_size > 0


def test_persistence_roundtrip(tmp_path):
    database = create_database(tmp_path / "test.sqlite3")
    writer = PersistenceWriter(database)
    writer.start()
    repo = Repository(database, writer)

    from banknifty_trading_app.core.models import Trade, new_id

    trade = Trade(
        trade_id=new_id("trade"),
        cycle_id="cyc_test",
        side=Side.BUY,
        lots=1,
        lot_size=30,
        entry_time=_dt.datetime(2026, 1, 5, 9, 30),
        entry_price=50000.0,
        exit_time=_dt.datetime(2026, 1, 5, 10, 30),
        exit_price=50100.0,
        pnl_points=100.0,
        pnl_money=3000.0,
        entry_reason="entry_signal",
        exit_reason="partial_target",
        status="CLOSED",
    )
    repo.log_trade(trade)
    writer.stop()  # drains

    rows = repo.all_trades()
    assert len(rows) == 1
    assert isinstance(rows[0], TradeRow)
    assert rows[0].pnl_money == 3000.0
