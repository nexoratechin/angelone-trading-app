"""Bar-replay backtester.

Crucially, this drives the *same* :class:`StrategyEngine` used live. Only the
data source and the fill mechanism differ. Bar replay feeds each bar's implied
path as synthetic ticks; ``intrabar`` decides the order of the adverse and
favourable extremes when a bar could hit both a stop and a target.

Approximations (documented, not hidden):
  * signals/levels come from Spot; fills come from the Futures price at the
    same timestamp,
  * within a bar, synthetic ticks use the bar's futures close (open for the
    opening tick) rather than reconstructing futures tick-by-tick.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..core.models import Fill, IntentKind, StrategyEventRecord, Trade
from ..core.state import StrategyState
from ..logging import get_logger
from ..market_data.daily_closes import compute_levels
from ..positions.book import PositionBook
from ..positions.ledger import TradeLedger
from ..rules import StrategySpec
from ..strategy.engine import StrategyEngine
from .costs import CostModel

log = get_logger("backtest.replay")


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    events: list[StrategyEventRecord] = field(default_factory=list)
    equity: list[tuple[_dt.datetime, float]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl_money for t in self.trades)


class Backtester:
    def __init__(
        self,
        spec: StrategySpec,
        cost_model: CostModel | None = None,
        intrabar: str = "conservative",
    ) -> None:
        self.spec = spec
        self.cost = cost_model or CostModel()
        self.intrabar = intrabar
        self.lot_size = spec.instrument.lot_size
        self._trades: list[Trade] = []
        self._events: list[StrategyEventRecord] = []

    # ------------------------------------------------------------------ run
    def run(
        self,
        spot_df: pd.DataFrame,
        futures_df: pd.DataFrame,
        expiry_dates: set[_dt.date] | None = None,
    ) -> BacktestResult:
        expiry_dates = expiry_dates or set()
        self._trades = []
        self._events = []

        spot = spot_df.copy()
        spot["date"] = pd.to_datetime(spot["timestamp"]).dt.date
        daily_closes = spot.groupby("date")["close"].last().sort_index()

        merged = spot.merge(
            futures_df[["timestamp", "open", "high", "low", "close"]],
            on="timestamp",
            suffixes=("_s", "_f"),
        )
        by_date = {d: g for d, g in merged.groupby("date")}

        engine = StrategyEngine(self.spec, on_event=self._events.append)
        book = PositionBook(self.lot_size)
        ledger = TradeLedger(self.lot_size, on_trade_close=self._trades.append)

        needed = max(self.spec.reference_offset_days, self.spec.sma_period)
        dates = list(daily_closes.index)
        prev_expiry = False
        charges_total = 0.0
        fills = 0

        for idx, day in enumerate(dates):
            if idx < needed:
                prev_expiry = day in expiry_dates
                continue
            prev_closes = [float(x) for x in daily_closes.iloc[:idx]]
            try:
                levels = compute_levels(
                    day,
                    prev_closes,
                    reference_offset_days=self.spec.reference_offset_days,
                    sma_period=self.spec.sma_period,
                    require_sma=True,
                )
            except Exception as exc:  # insufficient history
                log.warning("Skipping %s: %s", day, exc)
                prev_expiry = day in expiry_dates
                continue

            if idx == needed or prev_expiry:
                engine.start_new_cycle()
                ledger.set_cycle(engine.state.cycle_id)

            bars = by_date.get(day)
            if bars is None or bars.empty:
                prev_expiry = day in expiry_dates
                continue

            first = bars.iloc[0]
            is_last = day in expiry_dates
            engine.start_day(
                levels,
                first_spot=float(first["open_s"]),
                first_futures=float(first["open_f"]),
                is_last_trading_day=is_last,
                ts=first["timestamp"].to_pydatetime(),
            )

            for row in bars.itertuples(index=False):
                for spot_px, fut_px, ts in self._sequence(row, engine.state):
                    intents = engine.on_tick(spot_px, fut_px, ts)
                    for intent in intents:
                        charges_total += self._apply(intent, ledger, book)
                        fills += 1
            prev_expiry = is_last

        return BacktestResult(
            trades=self._trades,
            events=self._events,
            equity=self._equity_curve(),
            meta={
                "strategy": self.spec.version,
                "intrabar": self.intrabar,
                "fills": fills,
                "charges": round(charges_total, 2),
                "net_pnl": round(sum(t.pnl_money for t in self._trades), 2),
                "lot_size": self.lot_size,
            },
        )

    # ------------------------------------------------------------- internals
    def _sequence(self, row, state: StrategyState):
        o, h, low, c = float(row.open_s), float(row.high_s), float(row.low_s), float(row.close_s)
        fo, fc = float(row.open_f), float(row.close_f)
        ts = row.timestamp.to_pydatetime()
        favorable_first = self.intrabar == "optimistic"

        side = state.side
        if side is None:
            order = [o, (h if favorable_first else low), (low if favorable_first else h), c]
        elif side.value == "BUY":
            order = [o, h, low, c] if favorable_first else [o, low, h, c]
        else:
            order = [o, low, h, c] if favorable_first else [o, h, low, c]

        seq = []
        last = None
        for price in order:
            if price == last:
                continue
            last = price
            seq.append((price, fo if price is o else fc, ts))
        return seq

    def _apply(self, intent, ledger: TradeLedger, book: PositionBook) -> float:
        closing = intent.kind in (IntentKind.EXIT, IntentKind.PARTIAL_EXIT)
        txn_side = intent.side.opposite if closing else intent.side
        price = self.cost.fill_price(intent.futures_price, txn_side)
        quantity = intent.lots * self.lot_size
        charges = self.cost.charges(price, quantity, txn_side)
        fill = Fill(
            order_id=f"bt_{intent.intent_id}",
            intent_id=intent.intent_id,
            symbol=self.spec.instrument.symbol,
            side=txn_side,
            quantity=quantity,
            price=price,
            timestamp=intent.timestamp,
            reason=intent.reason,
            strategy_version=self.spec.version,
            charges=charges,
            meta=dict(intent.meta),
        )
        book.on_fill(fill)
        ledger.on_fill(fill)
        return charges

    def _equity_curve(self) -> list[tuple[_dt.datetime, float]]:
        cumulative = 0.0
        curve: list[tuple[_dt.datetime, float]] = []
        for trade in sorted(self._trades, key=lambda t: t.exit_time or t.entry_time):
            cumulative += trade.pnl_money
            curve.append((trade.exit_time or trade.entry_time, cumulative))
        return curve
