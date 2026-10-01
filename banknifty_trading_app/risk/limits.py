"""Build the configured set of risk gates from settings."""

from __future__ import annotations

from ..config import Settings
from ..rules.risk_gates import (
    MaxDailyLossGate,
    MaxLotsGate,
    MaxTradesPerDayGate,
    RiskGate,
    TradingHoursGate,
)


def build_gates(settings: Settings) -> list[RiskGate]:
    gates: list[RiskGate] = [
        MaxLotsGate(settings.max_lots),
        TradingHoursGate(settings.market_open, settings.market_close),
    ]
    if settings.max_trades_per_day is not None:
        gates.append(MaxTradesPerDayGate(settings.max_trades_per_day))
    if settings.max_daily_loss is not None:
        gates.append(MaxDailyLossGate(settings.max_daily_loss))
    return gates
