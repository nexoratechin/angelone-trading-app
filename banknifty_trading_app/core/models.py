"""Core domain types shared by every layer.

Nothing in here knows about Angel One, SQLite, or FastAPI. These are the
vocabulary of the system: sides, intents, orders, fills and trades.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal


# ---------------------------------------------------------------------------
# enums
# ---------------------------------------------------------------------------
class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY

    @property
    def sign(self) -> int:
        """+1 for a long, -1 for a short (used in P&L math)."""
        return 1 if self is Side.BUY else -1


class IntentKind(str, Enum):
    ENTER = "ENTER"
    EXIT = "EXIT"
    PARTIAL_EXIT = "PARTIAL_EXIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    COMPLETE = "COMPLETE"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class IntentStatus(str, Enum):
    CREATED = "CREATED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    FAILED = "FAILED"
    DUPLICATE = "DUPLICATE"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# market data
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Tick:
    symbol: str
    token: str
    ltp: float
    timestamp: datetime
    volume: int | None = None
    oi: float | None = None


@dataclass(slots=True)
class DayLevels:
    """Levels derived from daily Spot closes for a trading day.

    ``ref_close`` and ``prev_close`` are deliberately named to avoid the
    day-before-yesterday / yesterday confusion in the original notes.
    """

    trading_date: Any  # datetime.date
    ref_close: float  # "day before yesterday" close -> ENTRY trigger level
    prev_close: float  # "yesterday" close -> support/resistance & stop level
    sma: float | None  # 20-period SMA of daily Spot closes (fixed at prior close)


# ---------------------------------------------------------------------------
# strategy -> risk -> execution
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class TradeIntent:
    """A decision produced by the strategy engine and vetted by the risk layer."""

    kind: IntentKind
    side: Side
    lots: int
    reason: str
    spot_price: float
    futures_price: float
    timestamp: datetime
    strategy_version: str = ""
    intent_id: str = field(default_factory=lambda: new_id("intent"))
    status: IntentStatus = IntentStatus.CREATED
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OrderRequest:
    symbol: str
    token: str
    side: Side
    quantity: int
    order_type: str
    product_type: str
    price: float | None
    intent_id: str
    reason: str
    tag: str = ""
    order_id: str = field(default_factory=lambda: new_id("ordreq"))


@dataclass(slots=True)
class Fill:
    order_id: str
    intent_id: str
    symbol: str
    side: Side
    quantity: int  # in units (lots * lot_size)
    price: float
    timestamp: datetime
    reason: str = ""
    strategy_version: str = ""
    charges: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)
    fill_id: str = field(default_factory=lambda: new_id("fill"))


@dataclass(slots=True)
class OrderUpdate:
    order_id: str
    intent_id: str
    status: OrderStatus
    filled_quantity: int = 0
    average_price: float | None = None
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StrategyEventRecord:
    """A durable record of something the strategy did (entry, stop, partial...)."""

    event_type: str
    detail: str
    timestamp: datetime
    strategy_version: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Trade:
    """A completed leg used for reporting (entry -> exit)."""

    trade_id: str
    cycle_id: str
    side: Side
    lots: int
    lot_size: int
    entry_time: datetime
    entry_price: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    pnl_points: float = 0.0
    pnl_money: float = 0.0
    entry_reason: str = ""
    exit_reason: str = ""
    stop_level: float | None = None
    strategy_version: str = ""
    is_partial: bool = False
    is_reversal: bool = False
    status: Literal["OPEN", "CLOSED"] = "OPEN"
