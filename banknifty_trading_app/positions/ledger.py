"""Trade ledger: pairs entry/exit fills into reportable :class:`Trade` rows."""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable
from dataclasses import dataclass

from ..core.models import Fill, Side, Trade, new_id

ENTRY_REASONS = {"entry_signal", "reversal"}


@dataclass
class _OpenLot:
    trade_id: str
    side: Side
    entry_price: float
    entry_time: _dt.datetime
    entry_reason: str
    stop_level: float | None
    strategy_version: str
    is_reversal: bool
    entry_charges: float = 0.0


class TradeLedger:
    def __init__(
        self,
        lot_size: int,
        on_trade_close: Callable[[Trade], None] | None = None,
    ) -> None:
        self.lot_size = lot_size
        self.on_trade_close = on_trade_close or (lambda _t: None)
        self._open: list[_OpenLot] = []
        self._cycle_id = ""

    def set_cycle(self, cycle_id: str) -> None:
        self._cycle_id = cycle_id

    def on_fill(self, fill: Fill) -> list[Trade]:
        lots = max(1, fill.quantity // self.lot_size)
        closed: list[Trade] = []

        if fill.reason in ENTRY_REASONS:
            charges_per_lot = fill.charges / lots if lots else 0.0
            for _ in range(lots):
                self._open.append(
                    _OpenLot(
                        trade_id=new_id("trade"),
                        side=fill.side,
                        entry_price=fill.price,
                        entry_time=fill.timestamp,
                        entry_reason=fill.reason,
                        stop_level=fill.meta.get("stop"),
                        strategy_version=fill.strategy_version,
                        is_reversal=bool(fill.meta.get("is_reversal", fill.reason == "reversal")),
                        entry_charges=charges_per_lot,
                    )
                )
            return closed

        # closing / partial / reversal-out
        exit_charge_per_lot = fill.charges / lots if lots else 0.0
        remaining = lots
        while remaining > 0 and self._open:
            lot = self._open.pop(0)
            if lot.side is fill.side:
                # unexpected same-side close; skip defensively
                continue
            direction = 1 if lot.side is Side.BUY else -1
            pnl_points = (fill.price - lot.entry_price) * direction
            trade = Trade(
                trade_id=lot.trade_id,
                cycle_id=self._cycle_id,
                side=lot.side,
                lots=1,
                lot_size=self.lot_size,
                entry_time=lot.entry_time,
                entry_price=lot.entry_price,
                exit_time=fill.timestamp,
                exit_price=fill.price,
                pnl_points=pnl_points,
                pnl_money=pnl_points * self.lot_size - lot.entry_charges - exit_charge_per_lot,
                entry_reason=lot.entry_reason,
                exit_reason=fill.reason,
                stop_level=lot.stop_level,
                strategy_version=lot.strategy_version,
                is_partial=(fill.reason == "partial_target"),
                is_reversal=lot.is_reversal,
                status="CLOSED",
            )
            closed.append(trade)
            self.on_trade_close(trade)
            remaining -= 1
        return closed

    @property
    def open_lots(self) -> int:
        return len(self._open)

    def open_trades(self) -> list[Trade]:
        """Snapshot of still-open lots (for recovery/reporting)."""
        return [
            Trade(
                trade_id=lot.trade_id,
                cycle_id=self._cycle_id,
                side=lot.side,
                lots=1,
                lot_size=self.lot_size,
                entry_time=lot.entry_time,
                entry_price=lot.entry_price,
                entry_reason=lot.entry_reason,
                stop_level=lot.stop_level,
                strategy_version=lot.strategy_version,
                is_reversal=lot.is_reversal,
                status="OPEN",
            )
            for lot in self._open
        ]
