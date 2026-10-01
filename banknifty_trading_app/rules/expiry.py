"""Expiry rules: what to do on the last trading day of the futures contract."""

from __future__ import annotations

from .base import ExpiryRule, RuleContext
from .registry import register_expiry


@register_expiry("always")
class AlwaysSquareOff(ExpiryRule):
    """Flatten unconditionally on the last trading day; never carry a contract."""

    def should_square_off(self, ctx: RuleContext, is_last_trading_day: bool) -> bool:
        return bool(is_last_trading_day)


@register_expiry("never")
class NeverSquareOff(ExpiryRule):
    """Do nothing at expiry (only sensible in backtests over spot data)."""

    def should_square_off(self, ctx: RuleContext, is_last_trading_day: bool) -> bool:
        return False
