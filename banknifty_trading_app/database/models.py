"""Database schema (plain sqlite3 DDL) and read-side row types.

We deliberately use the standard-library ``sqlite3`` module rather than an ORM:
the write path is a simple batched ``executemany`` on a background thread, and
the read path is a couple of queries for reporting/recovery.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT,
    event_type    TEXT,
    detail        TEXT,
    strategy_version TEXT,
    payload_json  TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_ts   ON strategy_events(ts);
CREATE INDEX IF NOT EXISTS ix_events_type ON strategy_events(event_type);

CREATE TABLE IF NOT EXISTS orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id      TEXT,
    intent_id     TEXT,
    ts            TEXT,
    symbol        TEXT,
    side          TEXT,
    quantity      INTEGER,
    price         REAL,
    status        TEXT,
    reason        TEXT,
    strategy_version TEXT
);
CREATE INDEX IF NOT EXISTS ix_orders_ts ON orders(ts);

CREATE TABLE IF NOT EXISTS fills (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fill_id       TEXT,
    order_id      TEXT,
    intent_id     TEXT,
    ts            TEXT,
    symbol        TEXT,
    side          TEXT,
    quantity      INTEGER,
    price         REAL,
    reason        TEXT,
    strategy_version TEXT
);
CREATE INDEX IF NOT EXISTS ix_fills_ts ON fills(ts);

CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id      TEXT,
    cycle_id      TEXT,
    side          TEXT,
    lots          INTEGER,
    lot_size      INTEGER,
    entry_time    TEXT,
    entry_price   REAL,
    exit_time     TEXT,
    exit_price    REAL,
    pnl_points    REAL,
    pnl_money     REAL,
    entry_reason  TEXT,
    exit_reason   TEXT,
    stop_level    REAL,
    strategy_version TEXT,
    is_partial    INTEGER,
    is_reversal   INTEGER,
    status        TEXT
);
CREATE INDEX IF NOT EXISTS ix_trades_cycle ON trades(cycle_id);
CREATE INDEX IF NOT EXISTS ix_trades_entry ON trades(entry_time);
"""


def _parse_dt(value) -> _dt.datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, _dt.datetime):
        return value
    try:
        return _dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None


@dataclass
class TradeRow:
    trade_id: str
    cycle_id: str
    side: str
    lots: int
    lot_size: int
    entry_time: _dt.datetime | None
    entry_price: float
    exit_time: _dt.datetime | None
    exit_price: float | None
    pnl_points: float
    pnl_money: float
    entry_reason: str
    exit_reason: str
    stop_level: float | None
    strategy_version: str
    is_partial: bool
    is_reversal: bool
    status: str

    @classmethod
    def from_row(cls, row) -> "TradeRow":
        return cls(
            trade_id=row["trade_id"],
            cycle_id=row["cycle_id"],
            side=row["side"],
            lots=int(row["lots"] or 0),
            lot_size=int(row["lot_size"] or 0),
            entry_time=_parse_dt(row["entry_time"]),
            entry_price=float(row["entry_price"] or 0.0),
            exit_time=_parse_dt(row["exit_time"]),
            exit_price=(float(row["exit_price"]) if row["exit_price"] is not None else None),
            pnl_points=float(row["pnl_points"] or 0.0),
            pnl_money=float(row["pnl_money"] or 0.0),
            entry_reason=row["entry_reason"] or "",
            exit_reason=row["exit_reason"] or "",
            stop_level=(float(row["stop_level"]) if row["stop_level"] is not None else None),
            strategy_version=row["strategy_version"] or "",
            is_partial=bool(row["is_partial"]),
            is_reversal=bool(row["is_reversal"]),
            status=row["status"] or "CLOSED",
        )


@dataclass
class EventRow:
    ts: _dt.datetime | None
    event_type: str
    detail: str
    strategy_version: str
    payload_json: str = "{}"

    @classmethod
    def from_row(cls, row) -> "EventRow":
        return cls(
            ts=_parse_dt(row["ts"]),
            event_type=row["event_type"] or "",
            detail=row["detail"] or "",
            strategy_version=row["strategy_version"] or "",
            payload_json=row["payload_json"] or "{}",
        )
