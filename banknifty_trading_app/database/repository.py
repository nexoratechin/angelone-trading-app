"""Repository: high-level persistence API used by the app and reports."""

from __future__ import annotations

from .models import EventRow, TradeRow
from .session import Database
from .writer import PersistenceWriter, event_payload, fill_payload, order_payload, trade_payload


class Repository:
    def __init__(self, database: Database, writer: PersistenceWriter | None = None) -> None:
        self.database = database
        self.writer = writer

    # ------------------------------------------------------------------ writes
    def log_event(self, record) -> None:
        if self.writer:
            self.writer.submit("strategy_events", event_payload(record))

    def log_order(self, order_request, status: str = "SUBMITTED") -> None:
        if self.writer:
            self.writer.submit("orders", order_payload(order_request, status))

    def log_fill(self, fill) -> None:
        if self.writer:
            self.writer.submit("fills", fill_payload(fill))

    def log_trade(self, trade) -> None:
        if self.writer:
            self.writer.submit("trades", trade_payload(trade))

    # ------------------------------------------------------------------- reads
    def recent_trades(self, limit: int = 500) -> list[TradeRow]:
        rows = self.database.query(
            "SELECT * FROM trades ORDER BY entry_time DESC LIMIT ?", (limit,)
        )
        return [TradeRow.from_row(r) for r in rows]

    def all_trades(self) -> list[TradeRow]:
        rows = self.database.query("SELECT * FROM trades ORDER BY entry_time")
        return [TradeRow.from_row(r) for r in rows]

    def open_trades(self) -> list[TradeRow]:
        rows = self.database.query("SELECT * FROM trades WHERE status = 'OPEN'")
        return [TradeRow.from_row(r) for r in rows]

    def recent_events(self, limit: int = 1000) -> list[EventRow]:
        rows = self.database.query(
            "SELECT * FROM strategy_events ORDER BY ts DESC LIMIT ?", (limit,)
        )
        return [EventRow.from_row(r) for r in rows]
