"""Offline demo runner.

Feeds synthetic market data through the **real** live pipeline - strategy
engine, risk manager, order manager, paper executor, position book, trade
ledger and the batched SQLite writer - with no broker connection and no
credentials. Useful to watch the whole system run locally and to produce a
report + database you can inspect.

Contrast with ``backtest/replay.py``: that exercises the strategy engine only.
This exercises the live execution path (the paper broker), which is what you
would run against Angel One.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from pathlib import Path

import pandas as pd

from .angelone.instruments import Instrument
from .config import Settings
from .core.models import Tick
from .database.repository import Repository
from .database.session import create_database
from .database.writer import PersistenceWriter
from .execution.order_manager import OrderManager
from .execution.paper import PaperExecutor
from .logging import get_logger, get_trade_logger
from .market_data.daily_closes import compute_levels
from .market_data.store import MarketDataStore
from .positions.book import PositionBook
from .positions.ledger import TradeLedger
from .reports.excel import write_report
from .risk.kill_switch import KillSwitch
from .risk.manager import RiskManager
from .rules import StrategySpec, load_spec
from .strategy.engine import StrategyEngine

log = get_logger("demo")
tradelog = get_trade_logger()

SPOT_TOKEN = "SYNTH_SPOT"
FUT_TOKEN = "SYNTH_FUT"


class DemoRunner:
    def __init__(self, settings: Settings, spec: StrategySpec | None = None) -> None:
        self.settings = settings
        self.spec = spec or load_spec(settings.strategy_path())
        self.lot_size = self.spec.instrument.lot_size

        self.db = create_database(settings.db_path)
        self.writer = PersistenceWriter(self.db)
        self.repo = Repository(self.db, self.writer)
        self.kill_switch = KillSwitch(settings.kill_switch_file)
        self.risk = RiskManager(settings, self.kill_switch)

        self.store = MarketDataStore()
        self.book = PositionBook(self.lot_size)
        self.ledger = TradeLedger(self.lot_size, on_trade_close=self._on_trade_close)
        self.engine = StrategyEngine(self.spec, on_event=self.repo.log_event)

        self.instrument = Instrument(
            token=FUT_TOKEN, symbol=self.spec.instrument.symbol, name=self.spec.instrument.symbol,
            exchange="NFO", instrument_type="FUTIDX", lot_size=self.lot_size, tick_size=0.05,
        )
        self.spot = Instrument(
            token=SPOT_TOKEN, symbol=self.spec.instrument.spot_symbol, name=self.spec.instrument.spot_symbol,
            exchange="NSE", instrument_type="AMXIDX", lot_size=1, tick_size=0.05,
        )
        self.executor = PaperExecutor(settings, self.store)
        self.order_manager = OrderManager(
            settings, self.executor, self.instrument, self.lot_size,
            self.spec.instrument.product_type, on_fill=self._on_fill,
            on_order=self.repo.log_order,
        )
        self.trades = []
        self.feed = None  # demo has no broker feed; kept for dashboard compatibility

    # ------------------------------------------------------------- callbacks
    def _on_fill(self, fill) -> None:
        self.book.on_fill(fill)
        self.ledger.on_fill(fill)
        self.risk.on_fill(fill)
        self.repo.log_fill(fill)
        self.risk.set_open_lots(self.book.open_lots)
        self.risk.set_daily_pnl(self.book.snapshot().total_money)

    def _on_trade_close(self, trade) -> None:
        self.trades.append(trade)
        self.repo.log_trade(trade)

    # ------------------------------------------------------------------- run
    async def run(
        self,
        start: _dt.date,
        end: _dt.date,
        output: str | Path | None = None,
        verbose_every: int = 20,
        pace_s: float = 0.0,
    ) -> Path:
        from .backtest.synthetic import generate

        self.writer.start()
        log.info(
            "DEMO (offline, paper execution) strategy=%s %s..%s lot_size=%d",
            self.spec.version, start, end, self.lot_size,
        )
        spot_df, fut_df, expiries = generate(start, end)

        spot_df = spot_df.copy()
        spot_df["date"] = pd.to_datetime(spot_df["timestamp"]).dt.date
        daily_closes = spot_df.groupby("date")["close"].last().sort_index()
        merged = spot_df.merge(
            fut_df[["timestamp", "open", "high", "low", "close"]],
            on="timestamp", suffixes=("_s", "_f"),
        )
        by_date = {d: g for d, g in merged.groupby("date")}

        needed = max(self.spec.reference_offset_days, self.spec.sma_period)
        dates = list(daily_closes.index)
        prev_expiry = False
        day_count = 0

        for idx, day in enumerate(dates):
            if idx < needed:
                prev_expiry = day in expiries
                continue
            prev_closes = [float(x) for x in daily_closes.iloc[:idx]]
            try:
                levels = compute_levels(
                    day, prev_closes,
                    reference_offset_days=self.spec.reference_offset_days,
                    sma_period=self.spec.sma_period, require_sma=True,
                )
            except Exception:
                prev_expiry = day in expiries
                continue

            self.store.set_closes(self.spec.instrument.spot_symbol, prev_closes)
            if idx == needed or prev_expiry:
                self.engine.start_new_cycle()
                self.ledger.set_cycle(self.engine.state.cycle_id)

            bars = by_date.get(day)
            if bars is None or bars.empty:
                prev_expiry = day in expiries
                continue
            is_last = day in expiries
            first = bars.iloc[0]
            self.engine.start_day(
                levels, first_spot=float(first["open_s"]), first_futures=float(first["open_f"]),
                is_last_trading_day=is_last, ts=first["timestamp"].to_pydatetime(),
            )

            for row in bars.itertuples(index=False):
                ts = row.timestamp.to_pydatetime()
                for spot_px, fut_px in self._sequence(row, self.engine.state):
                    self.store.update(Tick(self.spot.symbol, SPOT_TOKEN, spot_px, ts))
                    self.store.update(Tick(self.instrument.symbol, FUT_TOKEN, fut_px, ts))
                    intents = self.engine.on_tick(spot_px, fut_px, ts)
                    for intent in intents:
                        await self._process(intent, ts)

            prev_expiry = is_last
            day_count += 1
            if pace_s:
                await asyncio.sleep(pace_s)
            if verbose_every and day_count % verbose_every == 0:
                snap = self.book.snapshot()
                log.info(
                    "  day %s processed (%d) | open=%s lots=%d realized=%.2f",
                    day, day_count, snap.side.value if snap.side else "-", snap.lots, snap.realized_money,
                )

        self.writer.stop()
        out = Path(output) if output else Path(self.settings.report_dir) / "demo_report.xlsx"
        write_report(
            out, self.trades, events=self.repo.recent_events(5000),
            meta={
                "mode": "DEMO (offline paper)",
                "strategy": self.spec.version,
                "start": str(start), "end": str(end),
                "lot_size": self.lot_size,
                "trading_days": day_count,
            },
            title=f"Bank Nifty DEMO Run - {self.spec.version}",
        )
        snap = self.book.snapshot()
        log.info(
            "DEMO complete: %d legs, realized P&L %.2f, report=%s",
            len(self.trades), snap.realized_money, out,
        )
        return out

    # ------------------------------------------------------------- helpers
    async def _process(self, intent, ts: _dt.datetime) -> None:
        ok, reason = self.risk.evaluate(intent, ts)
        if not ok:
            self.risk.mark_rejected(intent, reason)
            tradelog.info("REJECTED %s %s x%d (%s)", intent.kind.value, intent.side.value, intent.lots, reason)
            return
        await self.order_manager.handle_intent(intent)

    def _sequence(self, row, state):
        o, h, low, c = float(row.open_s), float(row.high_s), float(row.low_s), float(row.close_s)
        fo, fc = float(row.open_f), float(row.close_f)
        side = state.side
        if side is None:
            order = [o, low, h, c]
        elif side.value == "BUY":
            order = [o, low, h, c]
        else:
            order = [o, h, low, c]
        seq, last = [], None
        for price in order:
            if price == last:
                continue
            last = price
            seq.append((price, fo if price is o else fc))
        return seq
