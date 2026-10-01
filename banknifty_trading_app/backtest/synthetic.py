"""Synthetic 1-minute generator.

Lets you exercise the full pipeline (engine -> risk -> execution -> ledger ->
Excel) and run the one-year backtest without live market data. It is clearly
labelled as synthetic wherever it is used - it is NOT a market model.
"""

from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd

_SESSION_START = _dt.time(9, 15)
_SESSION_END = _dt.time(15, 29)


def trading_days(start: _dt.date, end: _dt.date) -> list[_dt.date]:
    days = pd.bdate_range(start, end).date
    return [d for d in days]


def monthly_expiry_days(days: list[_dt.date], expiry_weekday: int = 3) -> set[_dt.date]:
    """Last ``expiry_weekday`` (0=Mon) of each month, falling back to the month's last day."""
    df = pd.DataFrame({"date": days})
    df["month"] = df["date"].apply(lambda d: (d.year, d.month))
    expiries: set[_dt.date] = set()
    for _key, group in df.groupby("month"):
        matches = [d for d in group["date"] if d.weekday() == expiry_weekday]
        expiries.add(matches[-1] if matches else max(group["date"]))
    return expiries


def _intraday_timestamps(day: _dt.date) -> list[_dt.datetime]:
    start = _dt.datetime.combine(day, _SESSION_START)
    end = _dt.datetime.combine(day, _SESSION_END)
    stamps: list[_dt.datetime] = []
    cur = start
    while cur <= end:
        stamps.append(cur)
        cur += _dt.timedelta(minutes=1)
    return stamps


def generate(
    start: _dt.date,
    end: _dt.date,
    *,
    seed: int = 42,
    spot_start: float = 50_000.0,
    daily_vol: float = 0.008,
    expiry_weekday: int = 3,
) -> tuple[pd.DataFrame, pd.DataFrame, set[_dt.date]]:
    rng = np.random.default_rng(seed)
    days = trading_days(start, end)
    expiries = monthly_expiry_days(days, expiry_weekday)

    spot_rows: list[dict] = []
    fut_rows: list[dict] = []
    prev_close = spot_start

    for day in days:
        stamps = _intraday_timestamps(day)
        n = len(stamps)
        gap = rng.normal(0, daily_vol / 3)
        open_price = prev_close * (1 + gap)
        # random walk of per-minute returns around the open
        minute_vol = daily_vol / np.sqrt(n)
        returns = rng.normal(0, minute_vol, n)
        path = open_price * np.cumprod(1 + returns)

        highs = path * (1 + np.abs(rng.normal(0, minute_vol / 2, n)))
        lows = path * (1 - np.abs(rng.normal(0, minute_vol / 2, n)))
        opens = np.empty(n)
        opens[0] = open_price
        opens[1:] = path[:-1]
        highs = np.maximum.reduce([highs, opens, path])
        lows = np.minimum.reduce([lows, opens, path])

        premium = 40.0 + np.abs(rng.normal(0, 25.0, n))
        fut_path = path + premium

        for i, ts in enumerate(stamps):
            spot_rows.append(
                {
                    "timestamp": ts,
                    "open": float(opens[i]),
                    "high": float(highs[i]),
                    "low": float(lows[i]),
                    "close": float(path[i]),
                    "volume": int(abs(rng.normal(50_000, 10_000))),
                }
            )
            fut_rows.append(
                {
                    "timestamp": ts,
                    "open": float(opens[i] + premium[i]),
                    "high": float(highs[i] + premium[i]),
                    "low": float(lows[i] + premium[i]),
                    "close": float(fut_path[i]),
                    "volume": int(abs(rng.normal(20_000, 5_000))),
                }
            )
        prev_close = float(path[-1])

    return (
        pd.DataFrame(spot_rows),
        pd.DataFrame(fut_rows),
        expiries,
    )
