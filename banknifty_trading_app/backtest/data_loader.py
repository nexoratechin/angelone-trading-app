"""Historical candle loader with on-disk caching.

Angel One's historical endpoint has a limited look-back per interval, so we
fetch in chunks and cache each chunk to CSV. Cached files are reused on repeat
runs, which also respects API rate limits.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pandas as pd

from ..angelone.rest import AngelREST
from ..logging import get_logger

log = get_logger("backtest.data_loader")

_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


class HistoricalDataLoader:
    def __init__(
        self,
        rest: AngelREST | None,
        cache_dir: str | Path,
        chunk_days: int = 30,
    ) -> None:
        self.rest = rest
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.chunk_days = chunk_days

    def _cache_file(self, exchange: str, token: str, interval: str, start, end) -> Path:
        key = f"{exchange}_{token}_{interval}_{start:%Y%m%d}_{end:%Y%m%d}.csv"
        return self.cache_dir / key

    def load(
        self,
        exchange: str,
        token: str,
        interval: str,
        start: _dt.date,
        end: _dt.date,
        force: bool = False,
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + _dt.timedelta(days=self.chunk_days - 1), end)
            frames.append(self._load_chunk(exchange, token, interval, cursor, chunk_end, force))
            cursor = chunk_end + _dt.timedelta(days=1)
        if not frames:
            return pd.DataFrame(columns=_COLUMNS)
        df = pd.concat(frames, ignore_index=True).drop_duplicates("timestamp")
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    def _load_chunk(
        self,
        exchange: str,
        token: str,
        interval: str,
        start: _dt.date,
        end: _dt.date,
        force: bool,
    ) -> pd.DataFrame:
        cache_file = self._cache_file(exchange, token, interval, start, end)
        if cache_file.exists() and not force:
            return pd.read_csv(cache_file, parse_dates=["timestamp"])

        if self.rest is None:
            return pd.DataFrame(columns=_COLUMNS)

        from_dt = _dt.datetime.combine(start, _dt.time(9, 15))
        to_dt = _dt.datetime.combine(end, _dt.time(15, 30))
        rows = self.rest.candles(exchange, token, interval, from_dt, to_dt)
        if not rows:
            df = pd.DataFrame(columns=_COLUMNS)
        else:
            df = pd.DataFrame(rows, columns=_COLUMNS)
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            for col in ("open", "high", "low", "close", "volume"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna(subset=["close"]).reset_index(drop=True)
        df.to_csv(cache_file, index=False)
        log.info("Fetched %d candles %s->%s (%s)", len(df), start, end, cache_file.name)
        return df
