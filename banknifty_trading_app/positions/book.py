"""In-memory position book.

Updated only from confirmed fills, never from ticks (marks are separate). This
is the running source of truth for exposure and realised P&L; the database is
for durability and reporting, not for the hot path.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.models import Fill, Side


@dataclass(slots=True)
class PositionSnapshot:
    side: Side | None
    lots: int
    avg_price: float
    realized_points: float
    realized_money: float
    mark_price: float
    unrealized_points: float
    unrealized_money: float
    lot_size: int

    @property
    def total_money(self) -> float:
        return self.realized_money + self.unrealized_money


class PositionBook:
    def __init__(self, lot_size: int) -> None:
        self.lot_size = lot_size
        self.net = 0            # signed units
        self.avg_price = 0.0
        self.realized_points = 0.0
        self.realized_money = 0.0
        self.last_mark = 0.0
        self.last_fill_price = 0.0

    # ------------------------------------------------------------------ fills
    def on_fill(self, fill: Fill) -> float:
        """Apply a fill; returns the realised money delta for this fill."""
        signed = fill.quantity if fill.side is Side.BUY else -fill.quantity
        prev = self.net
        new = prev + signed
        realized_delta = 0.0

        if prev == 0 or (prev > 0) == (signed > 0):
            # opening or adding to the position -> recompute average
            total = abs(prev) + abs(signed)
            self.avg_price = (
                (self.avg_price * abs(prev) + fill.price * abs(signed)) / total
                if total
                else fill.price
            )
        else:
            # reducing / closing / flipping
            closing = min(abs(signed), abs(prev))
            direction = 1 if prev > 0 else -1
            realized_delta = (fill.price - self.avg_price) * closing * direction
            self.realized_money += realized_delta
            self.realized_points += realized_delta / self.lot_size
            if abs(signed) > abs(prev):
                self.avg_price = fill.price  # remainder opens the other side
            elif new == 0:
                self.avg_price = 0.0

        self.net = new
        self.last_fill_price = fill.price
        self.last_mark = fill.price
        return realized_delta

    # ------------------------------------------------------------------ marks
    def mark(self, price: float) -> None:
        if price > 0:
            self.last_mark = price

    def snapshot(self) -> PositionSnapshot:
        side = Side.BUY if self.net > 0 else (Side.SELL if self.net < 0 else None)
        lots = abs(self.net) // self.lot_size if self.lot_size else abs(self.net)
        unreal_points = self.net * (self.last_mark - self.avg_price) if self.net else 0.0
        return PositionSnapshot(
            side=side,
            lots=lots,
            avg_price=self.avg_price,
            realized_points=self.realized_points,
            realized_money=self.realized_money,
            mark_price=self.last_mark,
            unrealized_points=unreal_points / self.lot_size if self.lot_size else unreal_points,
            unrealized_money=unreal_points,
            lot_size=self.lot_size,
        )

    @property
    def open_lots(self) -> int:
        return abs(self.net) // self.lot_size if self.lot_size else abs(self.net)

    @property
    def has_position(self) -> bool:
        return self.net != 0
