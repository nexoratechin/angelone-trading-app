"""Risk gates used by the risk manager.

These are intentionally separate from the strategy rules: risk limits protect
the account regardless of which strategy version is active.
"""

from __future__ import annotations

import datetime as _dt
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..core.models import Side, TradeIntent


@dataclass(slots=True)
class RiskSnapshot:
    daily_pnl: float = 0.0
    trades_today: int = 0
    open_lots: int = 0


class RiskGate(ABC):
    """Returns ``(allowed, reason_if_blocked)``."""

    @abstractmethod
    def check(self, intent: TradeIntent, snap: RiskSnapshot, now: _dt.datetime) -> tuple[bool, str]:  # pragma: no cover
        raise NotImplementedError


class MaxLotsGate(RiskGate):
    def __init__(self, max_lots: int) -> None:
        self.max_lots = int(max_lots)

    def check(self, intent, snap, now):
        projected = snap.open_lots
        if intent.kind.value in ("ENTER",):
            projected += intent.lots
        if projected > self.max_lots:
            return False, f"max_lots_exceeded({projected}>{self.max_lots})"
        return True, ""


class MaxTradesPerDayGate(RiskGate):
    def __init__(self, max_trades: int) -> None:
        self.max_trades = int(max_trades)

    def check(self, intent, snap, now):
        if intent.kind.value in ("ENTER",) and snap.trades_today >= self.max_trades:
            return False, f"max_trades_per_day_reached({snap.trades_today})"
        return True, ""


class MaxDailyLossGate(RiskGate):
    def __init__(self, max_loss: float) -> None:
        self.max_loss = abs(float(max_loss))

    def check(self, intent, snap, now):
        if snap.daily_pnl <= -self.max_loss:
            return False, f"max_daily_loss_reached({snap.daily_pnl:.2f})"
        return True, ""


class TradingHoursGate(RiskGate):
    def __init__(self, market_open: str = "09:15", market_close: str = "15:30") -> None:
        self.open = _dt.time.fromisoformat(market_open)
        self.close = _dt.time.fromisoformat(market_close)

    def check(self, intent, snap, now):
        t = now.timetz().replace(tzinfo=None)
        if not (self.open <= t <= self.close):
            return False, f"outside_trading_hours({t.isoformat()})"
        return True, ""


class DuplicateGuard:
    """Tracks seen intent ids so the same signal can never be sent twice."""

    def __init__(self, max_remembered: int = 20_000) -> None:
        self._seen: set[str] = set()
        self._max = max_remembered

    def is_duplicate(self, intent: TradeIntent) -> bool:
        if intent.intent_id in self._seen:
            return True
        if len(self._seen) >= self._max:
            self._seen.clear()
        self._seen.add(intent.intent_id)
        return False
