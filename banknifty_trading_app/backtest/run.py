"""Backtest orchestration: synthetic (no broker needed) and historical modes.

Both modes run the shared strategy engine and produce the same Excel report as
live sessions, so backtests and live behaviour are directly comparable.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pandas as pd

from ..config import Settings
from ..logging import get_logger
from ..reports.excel import write_report
from ..rules import StrategySpec
from .costs import CostModel
from .data_loader import HistoricalDataLoader
from .replay import BacktestResult, Backtester
from .synthetic import generate as generate_synthetic

log = get_logger("backtest.run")


def _parse_date(value: str | _dt.date) -> _dt.date:
    if isinstance(value, _dt.date):
        return value
    return _dt.date.fromisoformat(value)


def default_range(settings: Settings) -> tuple[_dt.date, _dt.date]:
    end = _parse_date(settings.backtest_end) if settings.backtest_end else _dt.date.today()
    start = _parse_date(settings.backtest_start) if settings.backtest_start else end - _dt.timedelta(days=365)
    return start, end


def run_synthetic(
    settings: Settings,
    spec: StrategySpec,
    start: _dt.date | None = None,
    end: _dt.date | None = None,
    output: str | Path | None = None,
) -> BacktestResult:
    start, end = (start, end) if start and end else default_range(settings)
    spot_df, futures_df, expiries = generate_synthetic(start, end)
    cost = CostModel(slippage_points=settings.paper_slippage_points)
    bt = Backtester(spec, cost_model=cost, intrabar=settings.backtest_intrabar)
    result = bt.run(spot_df, futures_df, expiries)
    result.meta.update({"data": "synthetic", "start": str(start), "end": str(end)})
    _write(settings, spec, result, output)
    return result


def run_historical(
    settings: Settings,
    spec: StrategySpec,
    start: _dt.date | None = None,
    end: _dt.date | None = None,
    output: str | Path | None = None,
) -> BacktestResult:
    """Historical mode using Angel One candles.

    Spot drives signals; a stitched near-month futures series drives fills.
    Requires valid credentials (historical endpoints are authenticated).
    """
    from ..angelone.auth import AngelAuth
    from ..angelone.instruments import InstrumentMaster
    from ..angelone.rest import AngelREST

    start, end = (start, end) if start and end else default_range(settings)
    auth = AngelAuth(settings)
    rest = AngelREST(settings, auth)
    master = InstrumentMaster(settings.instrument_cache_path, settings.angel_instrument_master_url)
    loader = HistoricalDataLoader(rest, Path(settings.data_dir) / "history")

    spot_inst = master.resolve_spot(spec.instrument.spot_symbol, spec.instrument.spot_exchange)
    spot_df = loader.load(spot_inst.exchange, spot_inst.token, settings.backtest_interval, start, end)

    futures_df, expiries = _load_stitched_futures(master, loader, spec, settings, start, end)

    cost = CostModel(slippage_points=settings.paper_slippage_points)
    bt = Backtester(spec, cost_model=cost, intrabar=settings.backtest_intrabar)
    result = bt.run(spot_df, futures_df, expiries)
    result.meta.update({"data": "angel_one", "start": str(start), "end": str(end)})
    _write(settings, spec, result, output)
    return result


def _load_stitched_futures(master, loader, spec, settings, start, end):
    contracts = master.list_futures(
        spec.instrument.symbol, spec.instrument.exchange, include_expired=True
    )
    frames: list[pd.DataFrame] = []
    expiries: set[_dt.date] = set()
    for inst in contracts:
        if inst.expiry is None:
            continue
        if inst.expiry < start or inst.expiry > end + _dt.timedelta(days=45):
            continue
        df = loader.load(inst.exchange, inst.token, settings.backtest_interval, start, inst.expiry)
        if df.empty:
            continue
        df = df[df["timestamp"] <= pd.Timestamp(inst.expiry) + pd.Timedelta(hours=15, minutes=30)]
        if df.empty:
            continue
        last_day = pd.to_datetime(df["timestamp"]).dt.date.max()
        expiries.add(last_day)
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"]), expiries
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    return combined.reset_index(drop=True), expiries


def _write(settings: Settings, spec: StrategySpec, result: BacktestResult, output) -> Path:
    out = Path(output) if output else (
        Path(settings.report_dir) / f"backtest_{spec.version}_{_dt.date.today():%Y%m%d}.xlsx"
    )
    meta = dict(result.meta)
    meta["strategy_version"] = spec.version
    meta["description"] = spec.description
    path = write_report(
        out,
        result.trades,
        events=result.events,
        meta=meta,
        title=f"Bank Nifty Backtest - {spec.version}",
    )
    result.meta["report_path"] = str(path)
    return path
